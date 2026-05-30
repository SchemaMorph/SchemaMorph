"""
oracle/vertical_oracle.py

VerticalOracle: schema-relaxation oracle built on top of subset-style
query generation and subset verification.

Design:
  1. Build two main tables with the same column set:
     - S1 keeps the stricter schema, including injected UNIQUE constraints.
     - S2 relaxes S1 by dropping selected NOT NULL / UNIQUE constraints.
  2. Keep auxiliary tables fixed and shared by both sides.
  3. Replay the same INSERT workload into S1 and S2.
     The workload is biased to produce:
       - NULLs on relaxed non-unique columns
       - hot duplicate values on relaxed UNIQUE columns
     so some rows fail in S1 but succeed in S2.
  4. Because S2 accepts every S1 row plus additional rows, S1 is expected to
     be a row-multiset subset of S2.
  5. Generate row-preserving queries with SubsetQueryGenerator on S1, rewrite
     only the main-table name for S2, and verify monotone relations.
"""

import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from generate_random_sql import create_sample_tables, generate_create_table_sql
from oracle.subset_oracle import (
    IgnorableQueryRuntimeError,
    ConnectionLevelQueryError,
    MIN_BASELINE_QUERIES,
    MAX_QUERY_GEN_ATTEMPTS,
    _RUNTIME_ERROR_CODES,
    _RUNTIME_ERROR_PATTERNS,
    TARGET_BASELINE_QUERIES,
    ColDef,
    DMLEffectSnapshot,
    DMLEffectSpec,
    QuerySnapshot,
    QuerySpec,
    SkewProfile,
    SubsetOracle,
    _INDEXABLE_TYPES,
    _STRING_LIKE_TYPES,
    _TEMPORAL_TYPES,
)
from oracle.vertical_query_gen import VerticalQueryGenerator
from data_structures.db_dialect import get_current_dialect, set_current_dialect


INSERT_WORKLOAD_ROWS = 500
BOOTSTRAP_ROWS = 100
MIN_S1_ROWS = 24
MIN_S2_ONLY_ROWS = 12
NULL_STRESS_PROB = 0.35
MAX_NULL_STRESS_COLS = 2
UNIQUE_DUP_PROB = 0.40
COMPOSITE_DUP_PROB = 0.28
BOUNDARY_LITERAL_PROB = 0.15
UNCHANGED_PLAN_VERIFY_PROB = 0.15
MAX_SINGLE_UNIQUES = 2
VERTICAL_TARGET_BASELINE_QUERIES = max(TARGET_BASELINE_QUERIES, 8)
VERTICAL_QUERY_GEN_ATTEMPTS = max(MAX_QUERY_GEN_ATTEMPTS, 240)
RELAXED_BIAS_MIN_QUERIES = max(1, VERTICAL_TARGET_BASELINE_QUERIES // 2)
AUX_RISKY_SEEDS_PER_TABLE = 8
USE_UNIQUE_PROB = 0.70
USE_CHECK_PROB = 0.50
USE_FK_PROB = 0.40
MAX_CHECK_CONSTRAINTS = 2
MAX_FK_RELAXATIONS = 1
CHECK_VIOLATION_PROB = 0.30
FK_INVALID_PROB = 0.32
_UNIQUE_SAFE_TYPES = frozenset({
    'INT', 'VARCHAR', 'CHAR', 'ENUM', 'SET',
    'DATE', 'DATETIME', 'TIMESTAMP', 'TIME', 'YEAR', 'DECIMAL',
})
_CHECK_SAFE_TYPES = frozenset({'INT', 'FLOAT', 'DOUBLE', 'DECIMAL'})
_FK_SAFE_TYPES = frozenset({'INT'})


@dataclass
class ReplayStats:
    attempted: int = 0
    s1_success: int = 0
    s1_fail: int = 0
    s2_success: int = 0
    s2_fail: int = 0
    check_fail: int = 0
    fk_fail: int = 0


@dataclass
class RelaxationProfile:
    relaxed_cols: Set[str]
    null_relaxed_cols: Set[str]
    single_unique_cols: List[str]
    composite_unique_groups: List[Tuple[str, ...]]
    check_relaxed_cols: List[str]
    fk_relaxed_map: Dict[str, Tuple[str, str]]
    s2_index_groups: List[Tuple[str, ...]]

class VerticalOracle(SubsetOracle):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.total_baseline_queries = 0

    def run(self) -> dict:
        uid = uuid.uuid4().hex[:8]
        self._sql_log = []

        db_type = self.db_config.get('db_type', 'MYSQL').upper()
        set_current_dialect(db_type)
        self._dialect = get_current_dialect()

        family = self._dialect.optimizer_family()
        self._ignorable_codes = self._RUNTIME_ERROR_CODES.get(  # type: ignore[attr-defined]
            family, self._RUNTIME_ERROR_CODES['mysql']          # type: ignore[attr-defined]
        )
        self._ignorable_patterns = self._RUNTIME_ERROR_PATTERNS.get(  # type: ignore[attr-defined]
            family, self._RUNTIME_ERROR_PATTERNS['mysql']             # type: ignore[attr-defined]
        )
        if self.enable_known_mysql_date_index_string_eq_workaround is None:
            self._enable_known_mysql_date_index_string_eq_workaround = (
                family in ('mysql', 'percona')
            )
        else:
            self._enable_known_mysql_date_index_string_eq_workaround = (
                self.enable_known_mysql_date_index_string_eq_workaround
            )
        if self._ab.disable_known_bug_suppression:
            self._enable_known_mysql_date_index_string_eq_workaround = False

        round_stats = {
            'round_id': uid,
            'baseline_queries': 0,
            'select_baselines': 0,
            'dml_baselines': 0,
            'queries': 0,
            'select_queries': 0,
            'dml_queries': 0,
            'bugs': 0,
            'plan_changes': 0,
            'select_plan_changes': 0,
            'dml_plan_changes': 0,
            'skipped': False,
            's1_rows': 0,
            's2_rows': 0,
            's2_only_rows': 0,
            'insert_attempts': 0,
            's1_accepts': 0,
            's2_accepts': 0,
            'trace_attempts': 0,
            'trace_successes': 0,
            'trace_truncations': 0,
            'total_considered_plans': 0,
        }

        self._log_dir = os.path.join('invalid_mutation', db_type)
        os.makedirs(self._log_dir, exist_ok=True)

        self._log(f"\n{'=' * 60}")
        self._log(f" VERTICAL ORACLE round #{uid}")
        self._log(f"{'=' * 60}")
        self._log(
            " Workload model: identical INSERT replay on strict S1 and relaxed S2"
        )
        self._log(
            " Known MySQL DATE/YEAR-string workaround: "
            f"{'ON' if self._enable_known_mysql_date_index_string_eq_workaround else 'OFF'}"
        )
        self._log(
            f" Ablation config: {self._ab.name} / trace={'ON' if self._ab.enable_trace else 'OFF'} / "
            f"known_bug_suppression={'OFF' if self._ab.disable_known_bug_suppression else 'ON'}"
        )

        conn = self._connect()
        if conn is None:
            self._log("  [SKIP] Cannot connect to database.")
            round_stats['skipped'] = True
            return round_stats

        s1_name, s2_name, aux_name_map, vs_tables, main_vs_table = self._build_vertical_name_map(uid)
        all_actual_names = [s1_name, s2_name] + list(aux_name_map.values())

        try:
            self._log("\n[Step 1] Creating tables ...")
            main_cols, relax_profile = self._create_vertical_tables(
                conn, main_vs_table, vs_tables, s1_name, s2_name, aux_name_map
            )
            if not relax_profile.relaxed_cols:
                self._log("  [SKIP] No relaxable constraints available.")
                round_stats['skipped'] = True
                return round_stats

            numeric_cols = [
                c for c in main_cols if c.data_type in ('INT', 'FLOAT', 'DOUBLE', 'DECIMAL')
            ]
            pred_col = self._choose_predicate_col(main_cols, relax_profile)
            indexed_cols = self._choose_indexed_cols(main_cols, pred_col, relax_profile)
            if self._ab.opt2_index_and_analyze:
                self._create_matching_indexes(conn, s1_name, main_cols, indexed_cols, uid, 's1')
                self._create_matching_indexes(
                    conn, s2_name, main_cols, indexed_cols, uid, 's2', relax_profile.s2_index_groups
                )
            else:
                self._log("  [ablation] opt2 off: skipping vertical index creation")
                indexed_cols = set()
            for c in main_cols:
                c.is_indexed = c.name in indexed_cols

            skew = self._build_safe_skew_profile(main_cols, pred_col, relax_profile)
            self._log(f"  Relaxed columns in S2: {sorted(relax_profile.relaxed_cols)}")
            self._log(f"  NULL-relaxed columns: {sorted(relax_profile.null_relaxed_cols)}")
            self._log(f"  Relaxed UNIQUE columns: {relax_profile.single_unique_cols}")
            self._log(f"  Relaxed composite UNIQUE groups: {relax_profile.composite_unique_groups}")
            self._log(f"  CHECK-relaxed columns: {sorted(relax_profile.check_relaxed_cols)}")
            self._log(f"  FK-relaxed mapping: {relax_profile.fk_relaxed_map}")
            self._log(f"  Indexed columns: {sorted(indexed_cols)}")
            self._log(f"  Predicate column: {pred_col.name}")

            aux_vs_tables = [tbl for tbl in vs_tables if tbl.name != main_vs_table.name]
            if not self._has_risky_pair(main_cols, aux_vs_tables, relax_profile):
                self._log("  [SKIP] No risky relaxed-column / aux-string pair available.")
                round_stats['skipped'] = True
                return round_stats

            self._log("\n[Step 2] Populating auxiliary tables ...")
            self._insert_aux_data(conn, aux_vs_tables, aux_name_map)
            seeded_aux_rows = 0
            if self._ab.opt4_type_conversion:
                seeded_aux_rows = self._seed_aux_risky_values(
                    conn, aux_vs_tables, aux_name_map, relax_profile, main_cols, skew
                )
            self._log(f"  Seeded aux risky rows: {seeded_aux_rows}")
            fk_valid_pools = self._collect_fk_valid_pools(conn, relax_profile)
            if relax_profile.fk_relaxed_map and any(not pool for pool in fk_valid_pools.values()):
                self._log("  [SKIP] FK-relaxed mapping has no valid referenced values.")
                round_stats['skipped'] = True
                return round_stats
            if fk_valid_pools:
                self._log(
                    "  FK valid pools: "
                    + ", ".join(f"{col}={len(pool)}" for col, pool in fk_valid_pools.items())
                )

            self._log("\n[Step 3] Replaying INSERT workload on S1 and S2 ...")
            replay_stats = self._replay_insert_workload(
                conn, s1_name, s2_name, main_cols, skew, relax_profile, fk_valid_pools
            )
            round_stats['insert_attempts'] = replay_stats.attempted
            round_stats['s1_accepts'] = replay_stats.s1_success
            round_stats['s2_accepts'] = replay_stats.s2_success

            s1_count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {s1_name}")
            s2_count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {s2_name}")
            if s1_count is None or s2_count is None:
                self._log("  [SKIP] Failed to collect row counts.")
                round_stats['skipped'] = True
                return round_stats

            round_stats['s1_rows'] = s1_count
            round_stats['s2_rows'] = s2_count
            round_stats['s2_only_rows'] = s2_count - s1_count
            self._log(
                f"  Workload summary: attempts={replay_stats.attempted}, "
                f"S1 ok/fail={replay_stats.s1_success}/{replay_stats.s1_fail}, "
                f"S2 ok/fail={replay_stats.s2_success}/{replay_stats.s2_fail}"
            )
            self._log(f"  Rows rejected by CHECK: {replay_stats.check_fail}")
            self._log(f"  Rows rejected by FK: {replay_stats.fk_fail}")
            self._log(
                f"  Main table rows: S1={s1_count}, S2={s2_count}, delta={s2_count - s1_count}"
            )

            if s1_count < MIN_S1_ROWS:
                self._log("  [SKIP] S1 did not accumulate enough accepted rows.")
                round_stats['skipped'] = True
                return round_stats
            required_growth = self._min_required_s2_only_rows(replay_stats, relax_profile)
            if s2_count - s1_count < required_growth:
                self._log("  [SKIP] Relaxed schema did not create enough S2-only rows.")
                round_stats['skipped'] = True
                return round_stats

            if not self._verify_table_subset(conn, s1_name, s2_name):
                self._log("  [SKIP] Main-table subset sanity check failed.")
                round_stats['skipped'] = True
                return round_stats

            self._log("\n[Step 4] ANALYZE TABLE ...")
            self._analyze_table(conn, s1_name)
            self._analyze_table(conn, s2_name)

            self._log("\n[Step 5] Building baseline queries on S1 ...")
            baselines = self._build_vertical_baselines(
                conn=conn,
                aux_vs_tables=aux_vs_tables,
                s1_name=s1_name,
                aux_name_map=aux_name_map,
                main_cols=main_cols,
                numeric_cols=numeric_cols,
                skew=skew,
                indexed_cols=indexed_cols,
                relax_profile=relax_profile,
            )
            dml_baselines = self._build_vertical_dml_baselines(
                conn=conn,
                aux_vs_tables=aux_vs_tables,
                s1_name=s1_name,
                aux_name_map=aux_name_map,
                main_cols=main_cols,
                skew=skew,
                relax_profile=relax_profile,
            )
            self._log(f"  Validated SELECT baselines: {len(baselines)}")
            self._log(f"  Validated DML-effect baselines: {len(dml_baselines)}")
            round_stats['baseline_queries'] = len(baselines)
            round_stats['select_baselines'] = len(baselines)
            round_stats['dml_baselines'] = len(dml_baselines)
            if len(baselines) < MIN_BASELINE_QUERIES:
                self._log("  [SKIP] Not enough valid baseline queries.")
                round_stats['skipped'] = True
                return round_stats

            self._log("\n[Step 6] Verifying S1 subset S2 ...")
            unchanged_plan_verify_prob = self._dialect.unchanged_plan_verify_prob()
            for i, (spec, s1_snap) in enumerate(baselines):
                s2_sql = spec.select_sql.replace(s1_name, s2_name)
                s2_spec = QuerySpec(table_name=s2_name, select_sql=s2_sql)
                s2_plan = self._capture_explain_traditional(conn, s2_sql)
                plan_changed = not self._plans_equivalent(s1_snap.explain_plan, s2_plan)
                effective_prob = (
                    unchanged_plan_verify_prob if self._ab.opt3_plan_filter else 1.0
                )
                if not plan_changed and random.random() > effective_prob:
                    self._log(f"  Query[{i + 1}] plan_changed={plan_changed}")
                    self._log(f"  Query[{i + 1}] S1 SQL: {spec.select_sql}")
                    self._log(f"  Query[{i + 1}] S2 SQL: {s2_sql}")
                    self._log(f"  Query[{i + 1}] Plan S1: {self._fmt_plan(s1_snap.explain_plan)}")
                    self._log(f"  Query[{i + 1}] Plan S2: {self._fmt_plan(s2_plan)}")
                    self._log(f"  Query[{i + 1}] plan unchanged, skipping.")
                    continue

                s2_snap = self._execute_snapshot(conn, s2_sql, numeric_cols)
                if s2_snap is None:
                    self._log(f"  Query[{i + 1}] skipped due to expected runtime error.")
                    continue

                s2_snap.explain_plan = s2_plan
                self._collect_trace_stats(conn, s2_sql, round_stats)
                if plan_changed:
                    round_stats['plan_changes'] += 1

                self._log(f"  Query[{i + 1}] plan_changed={plan_changed}")
                self._log(f"  Query[{i + 1}] S1 SQL: {spec.select_sql}")
                self._log(f"  Query[{i + 1}] S2 SQL: {s2_sql}")
                self._log(f"  Query[{i + 1}] Plan S1: {self._fmt_plan(s1_snap.explain_plan)}")
                self._log(f"  Query[{i + 1}] Plan S2: {self._fmt_plan(s2_snap.explain_plan)}")

                try:
                    self._verify(conn, s2_spec, s1_snap, s2_snap, numeric_cols)
                    round_stats['queries'] += 1
                    round_stats['select_queries'] += 1
                    if plan_changed:
                        round_stats['select_plan_changes'] += 1
                except IgnorableQueryRuntimeError as e:
                    self._log(f"  Query[{i + 1}] skipped during verification: {e}")
                except AssertionError as e:
                    round_stats['queries'] += 1
                    round_stats['select_queries'] += 1
                    round_stats['bugs'] += 1
                    if plan_changed:
                        round_stats['select_plan_changes'] += 1
                    self._log_bug(str(e), s2_spec, s1_snap, s2_snap, uid)

            for i, (spec, s1_snap) in enumerate(dml_baselines):
                s2_spec = self._rewrite_vertical_dml_spec(spec, s1_name, s2_name)
                s2_plan = self._capture_explain_dml(conn, s2_spec.dml_sql)
                plan_changed = not self._plans_equivalent(s1_snap.explain_plan, s2_plan)
                self._log(f"  DML[{i + 1}] plan_changed={plan_changed}")
                self._log(f"  DML[{i + 1}] S1 SQL: {spec.dml_sql}")
                self._log(f"  DML[{i + 1}] S2 SQL: {s2_spec.dml_sql}")
                self._log(f"  DML[{i + 1}] Plan S1: {self._fmt_plan(s1_snap.explain_plan)}")
                self._log(f"  DML[{i + 1}] Plan S2: {self._fmt_plan(s2_plan)}")

                effective_prob = (
                    unchanged_plan_verify_prob if self._ab.opt3_plan_filter else 1.0
                )
                if not plan_changed and random.random() > effective_prob:
                    self._log(f"  DML[{i + 1}] plan unchanged, skipping.")
                    continue

                s2_snap = self._execute_dml_effect_snapshot(conn, s2_spec)
                if s2_snap is None:
                    self._log(f"  DML[{i + 1}] skipped due to expected runtime error.")
                    continue

                s2_snap.explain_plan = s2_plan
                self._collect_trace_stats(conn, s2_spec.dml_sql, round_stats)
                if plan_changed:
                    round_stats['plan_changes'] += 1
                    round_stats['dml_plan_changes'] += 1

                try:
                    self._verify_dml_effect(spec, s1_snap, s2_snap)
                    round_stats['queries'] += 1
                    round_stats['dml_queries'] += 1
                except AssertionError as e:
                    round_stats['queries'] += 1
                    round_stats['dml_queries'] += 1
                    round_stats['bugs'] += 1
                    self._log_vertical_dml_bug(
                        str(e), spec, s2_spec, s1_snap, s2_snap, uid
                    )

            verified_queries = round_stats['queries']
            baseline_queries = (
                round_stats['select_baselines'] + round_stats['dml_baselines']
            )
            plan_changes = round_stats['plan_changes']
            verify_ratio = (verified_queries / baseline_queries * 100.0) if baseline_queries else 0.0
            plan_change_ratio = (plan_changes / baseline_queries * 100.0) if baseline_queries else 0.0
            self._log(
                "  Verification summary: "
                f"verified={verified_queries}/{baseline_queries} ({verify_ratio:.1f}%), "
                f"plan_changed={plan_changes}/{baseline_queries} ({plan_change_ratio:.1f}%), "
                f"bugs={round_stats['bugs']}"
            )
            self._log(
                "  Branch summary: "
                f"SELECT verified={round_stats['select_queries']}/{round_stats['select_baselines']}, "
                f"DML verified={round_stats['dml_queries']}/{round_stats['dml_baselines']}, "
                f"SELECT plan_changed={round_stats['select_plan_changes']}, "
                f"DML plan_changed={round_stats['dml_plan_changes']}"
            )
            if round_stats['bugs'] == 0 and verified_queries > 0:
                self._log(f"\n  All checks PASSED for round #{uid}")
            elif round_stats['bugs'] == 0:
                round_stats['skipped'] = True
                self._log(f"\n  [SKIP] Round #{uid} completed with 0 verified queries.")
            else:
                self._log(
                    f"\n  Round #{uid} completed with {round_stats['bugs']} bug(s) detected."
                )

        except ConnectionLevelQueryError as e:
            round_stats['skipped'] = True
            self._log(f"  [SKIP] round #{uid}: connection became unusable: {e}")
        except Exception as e:
            self._log(f"  [ERROR] round #{uid}: {e}")
        finally:
            # conn 可能已因 UTF-8 解码失败而损坏，先测试是否可用
            cleanup_conn = conn
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                # 连接已损坏，新开一个专用于清理
                cleanup_conn = self._connect()

            if cleanup_conn is not None:
                for name in all_actual_names:
                    self._drop_if_exists(cleanup_conn, name)
                if cleanup_conn is not conn:
                    try:
                        cleanup_conn.close()
                    except Exception:
                        pass

            try:
                conn.close()
            except Exception:
                pass
            self._sql_log = []
            self._log(f"{'=' * 60}\n")

        if not round_stats['skipped']:
            self.total_rounds += 1
            self.total_baseline_queries += round_stats['baseline_queries']
            self.total_queries += round_stats['queries']
            self.total_plan_changes += round_stats['plan_changes']
            self.total_select_queries += round_stats['select_queries']
            self.total_dml_queries += round_stats['dml_queries']
            self.total_select_plan_changes += round_stats['select_plan_changes']
            self.total_dml_plan_changes += round_stats['dml_plan_changes']
            self.total_bugs += round_stats['bugs']

        return round_stats

    _RUNTIME_ERROR_CODES = _RUNTIME_ERROR_CODES
    _RUNTIME_ERROR_PATTERNS = _RUNTIME_ERROR_PATTERNS

    def _build_vertical_name_map(self, uid: str):
        vs_tables = create_sample_tables()
        main_vs_table = vs_tables[0]
        s1_name = f"vert_s1_{uid}"
        s2_name = f"vert_s2_{uid}"
        aux_name_map = {
            tbl.name: f"vert_ref_{uid}_{tbl.name}"
            for tbl in vs_tables[1:]
        }
        return s1_name, s2_name, aux_name_map, vs_tables, main_vs_table

    def _create_vertical_tables(
        self,
        conn,
        main_vs_table,
        vs_tables,
        s1_name: str,
        s2_name: str,
        aux_name_map: Dict[str, str],
    ) -> Tuple[List[ColDef], RelaxationProfile]:
        main_cols = self._vs_table_to_coldefs(main_vs_table)
        aux_vs_tables = [tbl for tbl in vs_tables if tbl.name != main_vs_table.name]

        for tbl in aux_vs_tables:
            ddl = generate_create_table_sql(tbl)
            ddl = ddl.replace(f"CREATE TABLE {tbl.name}", f"CREATE TABLE {aux_name_map[tbl.name]}")
            ddl = self._normalize_generated_ddl(ddl)
            self._exec_ddl(conn, ddl)
            self._log(f"  Created aux table: {aux_name_map[tbl.name]}")

        s1_ddl = generate_create_table_sql(main_vs_table)
        s1_ddl = s1_ddl.replace(f"CREATE TABLE {main_vs_table.name}", f"CREATE TABLE {s1_name}")
        s1_ddl = self._normalize_generated_ddl(s1_ddl)
        enabled_relaxations = self._get_enabled_relaxation_types()
        relax_profile = self._build_relaxation_profile(
            main_cols,
            s1_ddl,
            main_vs_table.primary_key,
            aux_vs_tables,
            aux_name_map,
            enabled_relaxations,
            self._supports_check_constraints(conn),
        )
        s1_ddl = self._inject_s1_unique_constraints(s1_ddl, relax_profile, s1_name)
        s1_ddl = self._inject_s1_check_constraints(s1_ddl, relax_profile, s1_name)
        s1_ddl = self._inject_s1_fk_constraints(s1_ddl, relax_profile, s1_name)
        self._exec_ddl(conn, s1_ddl)
        self._log(f"  Created strict main table: {s1_name}")

        s2_ddl, relax_profile = self._build_relaxed_s2_ddl(
            s1_ddl=s1_ddl,
            s2_name=s2_name,
            pk_col_name=main_vs_table.primary_key,
            relax_profile=relax_profile,
        )
        self._exec_ddl(conn, s2_ddl)
        self._log(f"  Created relaxed main table: {s2_name}")

        return main_cols, relax_profile

    def _normalize_generated_ddl(self, ddl: str) -> str:
        ddl = self._strip_foreign_keys(ddl)
        return self._normalize_ddl_for_dialect(ddl)

    def _fix_trailing_commas(self, lines: List[str]) -> List[str]:
        result: List[str] = []
        for i, line in enumerate(lines):
            next_content = ''
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    next_content = lines[j].strip()
                    break
            if line.rstrip().endswith(',') and next_content.startswith(')'):
                line = line.rstrip()[:-1]
            result.append(line)
        return result

    def _scan_not_null_cols_from_ddl(
        self,
        ddl: str,
        pk_col_name: Optional[str],
    ) -> Set[str]:
        cols: Set[str] = set()
        for line in ddl.splitlines():
            col_name = self._ddl_column_name(line)
            upper = line.upper()
            if (
                col_name
                and col_name != pk_col_name
                and 'NOT NULL' in upper
                and 'AUTO_INCREMENT' not in upper
            ):
                cols.add(col_name)
        return cols

    def _build_relaxation_profile(
        self,
        cols: List[ColDef],
        s1_ddl: str,
        pk_col_name: Optional[str],
        aux_vs_tables,
        aux_name_map: Dict[str, str],
        enabled_relaxations: Set[str],
        supports_check_constraints: bool,
    ) -> RelaxationProfile:
        null_relaxed_cols = (
            self._scan_not_null_cols_from_ddl(s1_ddl, pk_col_name)
            if 'not_null' in enabled_relaxations
            else set()
        )
        existing_unique_cols = self._scan_existing_unique_cols_from_ddl(s1_ddl)
        unique_candidates = [
            c for c in cols
            if (
                not c.is_primary_key
                and c.data_type in _UNIQUE_SAFE_TYPES
                and c.name not in existing_unique_cols
            )
        ]
        random.shuffle(unique_candidates)
        preferred = [
            c for c in unique_candidates
            if c.data_type in (_TEMPORAL_TYPES + _STRING_LIKE_TYPES)
        ]
        if preferred:
            preferred_names = {c.name for c in preferred}
            others = [c for c in unique_candidates if c.name not in preferred_names]
            unique_candidates = preferred + others

        single_unique_cols: List[str] = []
        composite_unique_groups: List[Tuple[str, ...]] = []
        if 'unique' in enabled_relaxations and unique_candidates and random.random() < USE_UNIQUE_PROB:
            single_unique_cols = [
                c.name for c in unique_candidates[:min(MAX_SINGLE_UNIQUES, len(unique_candidates))]
            ]
            remaining = [c for c in unique_candidates if c.name not in single_unique_cols]
            if len(remaining) >= 2 and random.random() < 0.70:
                c1, c2 = random.sample(remaining, k=2)
                composite_unique_groups.append((c1.name, c2.name))

        selected_optional_cols = set(single_unique_cols)
        for group in composite_unique_groups:
            selected_optional_cols.update(group)

        check_relaxed_cols: List[str] = []
        check_candidates = [
            c for c in cols
            if (
                not c.is_primary_key
                and c.data_type in _CHECK_SAFE_TYPES
                and c.name not in selected_optional_cols
            )
        ]
        if (
            'check' in enabled_relaxations
            and supports_check_constraints
            and check_candidates
            and random.random() < USE_CHECK_PROB
        ):
            preferred_checks = [c for c in check_candidates if c.data_type in ('INT', 'DECIMAL')]
            ordered_checks = preferred_checks + [c for c in check_candidates if c not in preferred_checks]
            take = min(MAX_CHECK_CONSTRAINTS, len(ordered_checks))
            if take > 0:
                count = 1 if take == 1 else random.randint(1, take)
                check_relaxed_cols = [c.name for c in ordered_checks[:count]]
                selected_optional_cols.update(check_relaxed_cols)

        fk_relaxed_map: Dict[str, Tuple[str, str]] = {}
        fk_candidates: List[Tuple[str, Tuple[str, str]]] = []
        supports_foreign_keys = getattr(self._dialect, 'supports_foreign_keys', lambda: True)()
        if 'fk' in enabled_relaxations and supports_foreign_keys:
            fk_candidates = self._fk_candidate_mappings(
                cols,
                aux_vs_tables,
                aux_name_map,
                selected_optional_cols,
            )
        if fk_candidates and random.random() < USE_FK_PROB:
            for main_col, ref in fk_candidates[:MAX_FK_RELAXATIONS]:
                fk_relaxed_map[main_col] = ref
                selected_optional_cols.add(main_col)

        if (
            not null_relaxed_cols
            and not single_unique_cols
            and not composite_unique_groups
            and not check_relaxed_cols
            and not fk_relaxed_map
        ):
            if 'unique' in enabled_relaxations and unique_candidates:
                single_unique_cols = [unique_candidates[0].name]
            elif 'check' in enabled_relaxations and supports_check_constraints and check_candidates:
                check_relaxed_cols = [check_candidates[0].name]
            elif 'fk' in enabled_relaxations and fk_candidates:
                main_col, ref = fk_candidates[0]
                fk_relaxed_map[main_col] = ref

        relaxed_cols = set(null_relaxed_cols) | set(single_unique_cols)
        for group in composite_unique_groups:
            relaxed_cols.update(group)
        relaxed_cols.update(check_relaxed_cols)
        relaxed_cols.update(fk_relaxed_map.keys())

        s2_index_groups: List[Tuple[str, ...]] = []
        s2_index_groups.extend((name,) for name in single_unique_cols)
        s2_index_groups.extend(composite_unique_groups)
        s2_index_groups.extend((name,) for name in check_relaxed_cols)
        s2_index_groups.extend((name,) for name in fk_relaxed_map.keys())

        return RelaxationProfile(
            relaxed_cols=relaxed_cols,
            null_relaxed_cols=null_relaxed_cols,
            single_unique_cols=single_unique_cols,
            composite_unique_groups=composite_unique_groups,
            check_relaxed_cols=check_relaxed_cols,
            fk_relaxed_map=fk_relaxed_map,
            s2_index_groups=s2_index_groups,
        )

    def _inject_s1_unique_constraints(
        self,
        s1_ddl: str,
        relax_profile: RelaxationProfile,
        s1_name: str,
    ) -> str:
        lines = s1_ddl.splitlines()
        insert_at = len(lines) - 1
        for idx, line in enumerate(lines):
            if line.strip().startswith('PRIMARY KEY'):
                insert_at = idx
                break

        constraint_lines: List[str] = []
        for idx, col_name in enumerate(relax_profile.single_unique_cols):
            constraint_lines.append(
                f"    UNIQUE KEY `u_v_{s1_name}_{idx}` (`{col_name}`),"
            )
        for idx, group in enumerate(relax_profile.composite_unique_groups):
            cols_sql = ', '.join(f'`{name}`' for name in group)
            constraint_lines.append(
                f"    UNIQUE KEY `u_vc_{s1_name}_{idx}` ({cols_sql}),"
            )

        if not constraint_lines:
            return s1_ddl

        new_lines = lines[:insert_at] + constraint_lines + lines[insert_at:]
        return '\n'.join(self._fix_trailing_commas(new_lines))

    def _inject_s1_check_constraints(
        self,
        s1_ddl: str,
        relax_profile: RelaxationProfile,
        s1_name: str,
    ) -> str:
        if not relax_profile.check_relaxed_cols:
            return s1_ddl
        lines = s1_ddl.splitlines()
        insert_at = len(lines) - 1
        for idx, line in enumerate(lines):
            if line.strip().startswith('PRIMARY KEY'):
                insert_at = idx
                break
        constraint_lines = [
            f"    CONSTRAINT `chk_v_{s1_name}_{idx}` CHECK (`{col_name}` > 0),"
            for idx, col_name in enumerate(relax_profile.check_relaxed_cols)
        ]
        new_lines = lines[:insert_at] + constraint_lines + lines[insert_at:]
        return '\n'.join(self._fix_trailing_commas(new_lines))

    def _inject_s1_fk_constraints(
        self,
        s1_ddl: str,
        relax_profile: RelaxationProfile,
        s1_name: str,
    ) -> str:
        if not relax_profile.fk_relaxed_map:
            return s1_ddl
        lines = s1_ddl.splitlines()
        insert_at = len(lines) - 1
        for idx, line in enumerate(lines):
            if line.strip().startswith('PRIMARY KEY'):
                insert_at = idx
                break
        constraint_lines: List[str] = []
        for idx, (col_name, (ref_table, ref_col)) in enumerate(relax_profile.fk_relaxed_map.items()):
            constraint_lines.append(
                f"    CONSTRAINT `fk_v_{s1_name}_{idx}` FOREIGN KEY (`{col_name}`) "
                f"REFERENCES {self._qi(ref_table)} ({self._qi(ref_col)}),"
            )
        new_lines = lines[:insert_at] + constraint_lines + lines[insert_at:]
        return '\n'.join(self._fix_trailing_commas(new_lines))

    def _build_relaxed_s2_ddl(
        self,
        s1_ddl: str,
        s2_name: str,
        pk_col_name: Optional[str],
        relax_profile: RelaxationProfile,
    ) -> Tuple[str, RelaxationProfile]:
        ddl = re.sub(
            r'^\s*CREATE\s+TABLE\s+`?([A-Za-z0-9_]+)`?',
            f"CREATE TABLE {s2_name}",
            s1_ddl,
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        result_lines: List[str] = []
        for line in ddl.splitlines():
            col_name = self._ddl_column_name(line)
            upper = line.upper()
            if (
                col_name
                and col_name != pk_col_name
                and 'NOT NULL' in upper
                and 'AUTO_INCREMENT' not in upper
                and col_name in relax_profile.null_relaxed_cols
            ):
                line = re.sub(r'\bNOT\s+NULL\b', 'NULL', line, flags=re.IGNORECASE)
            if 'CHECK' in upper and 'CHK_V_' in upper:
                continue
            if 'FOREIGN KEY' in upper and 'FK_V_' in upper:
                continue
            if 'UNIQUE KEY' in upper or upper.strip().startswith('UNIQUE '):
                if self._ddl_relaxes_unique_line(line, relax_profile):
                    continue
            result_lines.append(line)
        return '\n'.join(self._fix_trailing_commas(result_lines)), relax_profile

    def _ddl_relaxes_unique_line(self, line: str, relax_profile: RelaxationProfile) -> bool:
        m = re.search(r'\(([^()]*)\)', line)
        if not m:
            return False
        cols = tuple(re.findall(r'`([^`]+)`', m.group(1)))
        if not cols:
            return False
        if len(cols) == 1 and cols[0] in relax_profile.single_unique_cols:
            return True
        return cols in relax_profile.composite_unique_groups

    def _scan_existing_unique_cols_from_ddl(self, ddl: str) -> Set[str]:
        cols: Set[str] = set()
        for line in ddl.splitlines():
            upper = line.upper()
            if 'UNIQUE KEY' not in upper and not upper.strip().startswith('UNIQUE '):
                continue
            m = re.search(r'\(([^()]*)\)', line)
            if not m:
                continue
            cols.update(re.findall(r'`([^`]+)`', m.group(1)))
        return cols

    def _ddl_column_name(self, line: str) -> Optional[str]:
        stripped = line.strip()
        if not stripped:
            return None
        upper = stripped.upper()
        for kw in (
            'PRIMARY KEY',
            'UNIQUE KEY',
            'UNIQUE INDEX',
            'KEY ',
            'INDEX ',
            'FOREIGN KEY',
            'CONSTRAINT',
        ):
            if upper.startswith(kw):
                return None
        m = re.match(r'`([^`]+)`', stripped)
        if not m:
            m = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s+', stripped)
        return m.group(1) if m else None

    def _qi(self, name: str) -> str:
        return f"`{name}`"

    def _get_enabled_relaxation_types(self) -> frozenset:
        family = self._dialect.optimizer_family()
        if family in ('mysql', 'mariadb', 'percona'):
            return frozenset({'not_null', 'unique', 'check', 'fk'})
        if family in ('tidb', 'oceanbase', 'polardb'):
            return frozenset({'not_null', 'unique'})
        return frozenset({'not_null', 'unique'})

    def _supports_check_constraints(self, conn) -> bool:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
            version_text = str(row[0]) if row and row[0] is not None else ''
        except Exception:
            return False

        m = re.match(r'(\d+)\.(\d+)\.(\d+)', version_text)
        if not m:
            return False
        major, minor, patch = map(int, m.groups())
        family = self._dialect.optimizer_family()
        if family in ('mysql', 'percona'):
            return (major, minor, patch) >= (8, 0, 16)
        if family == 'mariadb':
            return (major, minor, patch) >= (10, 2, 1)
        if family == 'tidb':
            return (major, minor, patch) >= (7, 2, 0)
        if family == 'oceanbase':
            return (major, minor, patch) >= (4, 2, 0)
        if family == 'polardb':
            return False
        return False

    def _fk_candidate_mappings(
        self,
        cols: List[ColDef],
        aux_vs_tables,
        aux_name_map: Dict[str, str],
        excluded_cols: Set[str],
    ) -> List[Tuple[str, Tuple[str, str]]]:
        main_candidates = [
            c for c in cols
            if (
                not c.is_primary_key
                and c.data_type in _FK_SAFE_TYPES
                and c.name not in excluded_cols
            )
        ]
        ref_targets: List[Tuple[str, str]] = []
        for tbl in aux_vs_tables:
            actual_name = aux_name_map[tbl.name]
            aux_cols = self._vs_table_to_coldefs(tbl)
            pk_col = next((c for c in aux_cols if c.is_primary_key and c.data_type in _FK_SAFE_TYPES), None)
            if pk_col is not None:
                ref_targets.append((actual_name, pk_col.name))

        pairs: List[Tuple[str, Tuple[str, str]]] = []
        for main_col in main_candidates:
            for ref_target in ref_targets:
                pairs.append((main_col.name, ref_target))
        random.shuffle(pairs)
        return pairs

    def _choose_indexed_cols(
        self,
        cols: List[ColDef],
        pred_col: ColDef,
        relax_profile: RelaxationProfile,
    ) -> Set[str]:
        indexed: Set[str] = set()
        if self._col_is_indexable(cols, pred_col.name):
            indexed.add(pred_col.name)
        indexed.update(name for name in relax_profile.relaxed_cols if self._col_is_indexable(cols, name))
        candidates = [
            c for c in cols
            if not c.is_primary_key and self._col_is_indexable(cols, c.name)
        ]
        if not indexed and candidates:
            indexed.add(random.choice(candidates).name)
        candidates = [c for c in candidates if c.name not in indexed]
        if candidates:
            extra = random.sample(candidates, k=min(random.randint(1, 2), len(candidates)))
            indexed.update(c.name for c in extra)
        return indexed

    def _col_is_indexable(self, cols: List[ColDef], name: str) -> bool:
        col = next((c for c in cols if c.name == name), None)
        return bool(col and super()._col_is_indexable(col))

    def _create_matching_indexes(
        self,
        conn,
        table_name: str,
        cols: List[ColDef],
        indexed_col_names: Set[str],
        uid: str,
        tag: str,
        extra_groups: Optional[List[Tuple[str, ...]]] = None,
    ):
        for col in cols:
            if col.name not in indexed_col_names:
                continue
            if not self._col_is_indexable(cols, col.name):
                continue
            index_name = f"i_v_{tag}_{uid}_{col.name}"
            ddl = f"CREATE INDEX {index_name} ON {table_name} ({self._index_expr(col)})"
            self._exec_ddl(conn, ddl, ignore_error=True)
        for idx, group in enumerate(extra_groups or []):
            exprs = []
            ok = True
            for name in group:
                col = next((c for c in cols if c.name == name), None)
                if col is None or not self._col_is_indexable(cols, name):
                    ok = False
                    break
                exprs.append(self._index_expr(col))
            if not ok or not exprs:
                continue
            ddl = (
                f"CREATE INDEX i_vg_{tag}_{uid}_{idx} ON {table_name} "
                f"({', '.join(exprs)})"
            )
            self._exec_ddl(conn, ddl, ignore_error=True)

    def _build_safe_skew_profile(
        self,
        cols: List[ColDef],
        pred_col: ColDef,
        relax_profile: RelaxationProfile,
    ) -> SkewProfile:
        hot_values_by_col: Dict[str, List[str]] = {}
        for c in cols:
            if c.name in relax_profile.check_relaxed_cols:
                hot_values_by_col[c.name] = self._check_compliant_hot_values(c)
            else:
                hot_values_by_col[c.name] = self._safe_hot_values(c)
        pred_hots = hot_values_by_col[pred_col.name] or [self._safe_literal_for_col(pred_col, 1)]
        primary = pred_hots[0]
        secondary = pred_hots[1] if len(pred_hots) > 1 else primary
        tertiary = pred_hots[2] if len(pred_hots) > 2 else secondary
        return SkewProfile(
            predicate_col=pred_col,
            primary_hot=primary,
            secondary_hot=secondary,
            tertiary_hot=tertiary,
            expansion_hot=tertiary,
            hot_values_by_col=hot_values_by_col,
        )

    def _check_compliant_hot_values(self, col: ColDef) -> List[str]:
        if col.data_type == 'INT':
            return [str(random.randint(1, 6)), str(random.randint(7, 12)), str(random.randint(13, 20))]
        if col.data_type in ('FLOAT', 'DOUBLE'):
            return [f"{v:.3f}" for v in (1.0, 2.5, 9.0)]
        if col.data_type == 'DECIMAL':
            return [f"{v:.2f}" for v in (1.0, 2.0, 9.0)]
        return self._safe_hot_values(col)

    def _safe_hot_values(self, col: ColDef) -> List[str]:
        if col.data_type == 'INT':
            base = random.randint(-8, 8)
            return [str(base), str(base + 1), str(base + 2)]
        if col.data_type in ('FLOAT', 'DOUBLE'):
            base = random.randint(-50, 50) / 10.0
            return [f"{base:.3f}", f"{base + 1.0:.3f}", f"{base + 2.0:.3f}"]
        if col.data_type == 'DECIMAL':
            base = random.randint(-500, 500) / 100.0
            return [f"{base:.2f}", f"{base + 1.0:.2f}", f"{base + 2.0:.2f}"]
        if col.data_type == 'DATE':
            return ["'1000-01-01'", "'2024-02-29'", "'9999-12-31'"]
        if col.data_type in ('DATETIME', 'TIMESTAMP'):
            return [
                "'1000-01-01 00:00:00'",
                "'2024-02-29 23:59:59'",
                "'9999-12-31 23:59:59'",
            ]
        if col.data_type == 'TIME':
            return ["'00:00:00'", "'12:34:56'", "'23:59:59'"]
        if col.data_type == 'YEAR':
            return ['1901', '2024', '2155']
        if col.data_type == 'ENUM':
            choices = self._declared_choices(col)
            if choices:
                return [f"'{choices[min(i, len(choices) - 1)]}'" for i in range(min(3, len(choices)))]
        if col.data_type == 'SET':
            choices = self._declared_choices(col)
            if choices:
                first = choices[0]
                joined = ",".join(choices[:min(2, len(choices))])
                return [f"'{first}'", f"'{joined}'", f"'{choices[-1]}'"]
        if col.data_type in _STRING_LIKE_TYPES:
            token = f"vhv_{random.randint(100, 999)}"
            return [f"'{token}'", "'01e0'", "'not-a-date'"]
        return [self._safe_literal_for_col(col, 0)]

    def _replay_insert_workload(
        self,
        conn,
        s1_name: str,
        s2_name: str,
        cols: List[ColDef],
        skew: SkewProfile,
        relax_profile: RelaxationProfile,
        fk_valid_pools: Dict[str, List[str]],
    ) -> ReplayStats:
        stats = ReplayStats()
        relaxed_defs = [c for c in cols if c.name in relax_profile.null_relaxed_cols]
        pk_col = next((c for c in cols if c.is_primary_key), cols[0])
        duplicate_single_hot = {
            name: self._duplicate_hot_literal(cols, name, skew)
            for name in relax_profile.single_unique_cols
        }
        composite_hot = {
            group: self._duplicate_hot_group(cols, group, skew)
            for group in relax_profile.composite_unique_groups
        }

        for row_id in range(INSERT_WORKLOAD_ROWS):
            stress_targets: Set[str] = set()
            if row_id >= BOOTSTRAP_ROWS and relaxed_defs and random.random() < NULL_STRESS_PROB:
                k = min(MAX_NULL_STRESS_COLS, len(relaxed_defs))
                pick_k = 1 if k == 1 else random.choices([1, 2], weights=[0.75, 0.25], k=1)[0]
                stress_targets = {
                    c.name for c in random.sample(relaxed_defs, k=min(pick_k, len(relaxed_defs)))
                }

            values: List[str] = []
            for col in cols:
                if col.name == pk_col.name:
                    values.append(str(1_000_000 + row_id))
                    continue
                if col.name in stress_targets:
                    values.append('NULL')
                    continue
                values.append(
                    self._safe_workload_value(
                        col,
                        skew,
                        row_id,
                        duplicate_single_hot,
                        composite_hot,
                        relax_profile,
                        fk_valid_pools,
                    )
                )

            s1_ok, s1_err = self._execute_insert_replay(
                conn,
                s1_name,
                cols,
                values,
                suppress_expected_rejection_log=True,
            )
            s2_ok, _ = self._execute_insert_replay(conn, s2_name, cols, values)

            stats.attempted += 1
            stats.s1_success += int(s1_ok)
            stats.s1_fail += int(not s1_ok)
            stats.s2_success += int(s2_ok)
            stats.s2_fail += int(not s2_ok)
            if not s1_ok and s2_ok and s1_err:
                err_lower = s1_err.lower()
                if 'check constraint' in err_lower or 'chk_v_' in err_lower:
                    stats.check_fail += 1
                if 'foreign key constraint fails' in err_lower or 'fk_v_' in err_lower:
                    stats.fk_fail += 1

            if s1_ok and not s2_ok:
                raise RuntimeError(
                    "Relaxed schema invariant violated: row succeeded in S1 but failed in S2."
                )

        return stats

    def _duplicate_hot_literal(self, cols: List[ColDef], col_name: str, skew: SkewProfile) -> str:
        hot_values = skew.hot_values_by_col.get(col_name, [])
        if hot_values:
            return hot_values[0]
        col = next(c for c in cols if c.name == col_name)
        return self._safe_literal_for_col(col, 0)

    def _duplicate_hot_group(
        self,
        cols: List[ColDef],
        group: Tuple[str, ...],
        skew: SkewProfile,
    ) -> Dict[str, str]:
        return {
            name: self._duplicate_hot_literal(cols, name, skew)
            for name in group
        }

    def _safe_workload_value(
        self,
        col: ColDef,
        skew: SkewProfile,
        row_id: int,
        duplicate_single_hot: Dict[str, str],
        composite_hot: Dict[Tuple[str, ...], Dict[str, str]],
        relax_profile: RelaxationProfile,
        fk_valid_pools: Dict[str, List[str]],
    ) -> str:
        if col.name in fk_valid_pools:
            valid_pool = fk_valid_pools[col.name]
            if (
                self._ab.opt5_constraint_bias
                and row_id >= BOOTSTRAP_ROWS
                and random.random() < FK_INVALID_PROB
            ):
                return self._invalid_fk_literal(valid_pool, row_id)
            if valid_pool:
                return random.choice(valid_pool)
        if (
            self._ab.opt5_constraint_bias
            and
            row_id >= BOOTSTRAP_ROWS
            and col.name in duplicate_single_hot
            and random.random() < UNIQUE_DUP_PROB
        ):
            return duplicate_single_hot[col.name]
        if (
            self._ab.opt5_constraint_bias
            and
            row_id >= BOOTSTRAP_ROWS
            and col.name in relax_profile.check_relaxed_cols
            and random.random() < CHECK_VIOLATION_PROB
        ):
            return self._check_violating_literal(col)
        if row_id >= BOOTSTRAP_ROWS:
            for group, literals in composite_hot.items():
                if (
                    self._ab.opt5_constraint_bias
                    and col.name in group
                    and random.random() < COMPOSITE_DUP_PROB
                ):
                    return literals[col.name]
        hot_values = skew.hot_values_by_col.get(col.name, [])
        if hot_values and random.random() < (0.65 if col.name == skew.predicate_col.name else 0.35):
            return random.choice(hot_values)
        if col.is_nullable and col.name not in relax_profile.single_unique_cols and random.random() < 0.08:
            return 'NULL'
        if (
            self._ab.opt4_type_conversion
            and row_id >= BOOTSTRAP_ROWS
            and random.random() < BOUNDARY_LITERAL_PROB
        ):
            boundary = self._boundary_literal(col)
            if boundary is not None:
                return boundary
        if col.name in relax_profile.check_relaxed_cols:
            return self._check_compliant_literal(col, random.randint(0, 9999))
        return self._safe_literal_for_col(col, random.randint(0, 9999))

    def _check_violating_literal(self, col: ColDef) -> str:
        if col.data_type == 'INT':
            return random.choice(['0', '-1', str(random.randint(-200, -2))])
        if col.data_type in ('FLOAT', 'DOUBLE'):
            return random.choice(['0.000', '-1.000', f"{-random.uniform(2.0, 200.0):.3f}"])
        if col.data_type == 'DECIMAL':
            return random.choice(['0.00', '-1.00', f"{-random.uniform(2.0, 200.0):.2f}"])
        return '0'

    def _check_compliant_literal(self, col: ColDef, salt: int) -> str:
        if col.data_type == 'INT':
            return str(1 + (salt % 200))
        if col.data_type in ('FLOAT', 'DOUBLE'):
            return f"{1.0 + ((salt % 1000) / 10.0):.3f}"
        if col.data_type == 'DECIMAL':
            return f"{1.0 + ((salt % 10000) / 100.0):.2f}"
        return self._safe_literal_for_col(col, salt)

    def _invalid_fk_literal(self, valid_pool: List[str], row_id: int) -> str:
        valid = set(valid_pool)
        candidate = str(9_500_000 + row_id)
        while candidate in valid:
            candidate = str(int(candidate) + 1)
        return candidate

    def _safe_literal_for_col(self, col: ColDef, salt: int) -> str:
        dt = col.data_type
        if dt == 'INT':
            return str((salt % 401) - 200)
        if dt in ('FLOAT', 'DOUBLE'):
            return f"{((salt % 2001) - 1000) / 10.0:.3f}"
        if dt == 'DECIMAL':
            return f"{((salt % 200001) - 100000) / 100.0:.2f}"
        if dt == 'DATE':
            year = 2000 + (salt % 20)
            month = 1 + (salt % 12)
            day = 1 + (salt % 28)
            return f"'{year:04d}-{month:02d}-{day:02d}'"
        if dt in ('DATETIME', 'TIMESTAMP'):
            year = 2000 + (salt % 20)
            month = 1 + (salt % 12)
            day = 1 + (salt % 28)
            hour = salt % 24
            minute = salt % 60
            second = (salt * 7) % 60
            return f"'{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'"
        if dt == 'TIME':
            hour = salt % 24
            minute = (salt * 3) % 60
            second = (salt * 7) % 60
            return f"'{hour:02d}:{minute:02d}:{second:02d}'"
        if dt == 'YEAR':
            return str(2000 + (salt % 25))
        if dt == 'ENUM':
            choices = self._declared_choices(col)
            if choices:
                return f"'{choices[salt % len(choices)]}'"
        if dt == 'SET':
            choices = self._declared_choices(col)
            if choices:
                take = 1 + (salt % len(choices))
                return "'" + ",".join(choices[:take]) + "'"
        if dt in _STRING_LIKE_TYPES:
            max_len = max(1, min(col.varchar_len, 24))
            token = f"v_{salt}"
            if len(token) > max_len:
                token = token[:max_len]
            return f"'{token}'"
        declared = (col.declared_type or '').upper()
        if 'JSON' in declared:
            return "'{}'"
        if 'BIT' in declared:
            return "b'1'"
        if 'BLOB' in declared or 'BINARY' in declared:
            return "X'01'"
        return 'NULL'

    def _boundary_literal(self, col: ColDef) -> Optional[str]:
        dt = col.data_type
        declared = (col.declared_type or '').upper()
        if dt == 'INT':
            if 'UNSIGNED' in declared:
                return random.choice(['0', '1', '2147483647', '4294967295'])
            return random.choice(['0', '1', '-1', '2147483647', '-2147483648'])
        if dt in ('FLOAT', 'DOUBLE'):
            return random.choice([
                '0.0',
                '-0.0',
                '1.0',
                '-1.0',
                '3.4028234E38',
                '-3.4028234E38',
            ])
        if dt == 'DECIMAL':
            literals = self._decimal_boundary_literals(col)
            return random.choice(literals) if literals else None
        if dt in _STRING_LIKE_TYPES:
            return random.choice([
                "''",
                "'NULL'",
                "'0'",
                "'01e0'",
                "'not-a-date'",
                "'2023-01-01'",
                "'%'",
                "'_'",
            ])
        if dt == 'DATE':
            return random.choice([
                "'1000-01-01'",
                "'9999-12-31'",
                "'2024-02-29'",
            ])
        if dt in ('DATETIME', 'TIMESTAMP'):
            return random.choice([
                "'1000-01-01 00:00:00'",
                "'2024-02-29 23:59:59'",
                "'9999-12-31 23:59:59'",
            ])
        if dt == 'TIME':
            return random.choice([
                "'00:00:00'",
                "'23:59:59'",
            ])
        if dt == 'YEAR':
            return random.choice(['1901', '2024', '2155'])
        return None

    def _decimal_boundary_literals(self, col: ColDef) -> List[str]:
        declared = (col.declared_type or '').upper()
        m = re.search(r'DECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', declared)
        if m:
            precision = max(1, int(m.group(1)))
            scale = max(0, int(m.group(2)))
            int_digits = max(1, precision - scale)
            int_part = '9' * int_digits
            frac_part = '9' * scale
            max_lit = f"{int_part}.{frac_part}" if scale else int_part
            zero_lit = f"0.{('0' * scale)}" if scale else '0'
            one_lit = f"1.{('0' * scale)}" if scale else '1'
            return [zero_lit, one_lit, f"-{one_lit}", max_lit, f"-{max_lit}"]
        return ['0.00', '1.00', '-1.00', '99999999.99', '-99999999.99']

    def _has_risky_pair(
        self,
        main_cols: List[ColDef],
        aux_vs_tables,
        relax_profile: RelaxationProfile,
    ) -> bool:
        if relax_profile.check_relaxed_cols or relax_profile.fk_relaxed_map:
            return True
        relaxed_main = [
            c for c in main_cols
            if c.name in relax_profile.relaxed_cols
            and c.data_type in (_TEMPORAL_TYPES + _STRING_LIKE_TYPES + ('INT', 'FLOAT', 'DOUBLE', 'DECIMAL'))
        ]
        if not relaxed_main:
            return False
        for tbl in aux_vs_tables:
            aux_cols = self._vs_table_to_coldefs(tbl)
            if any(c.data_type in _STRING_LIKE_TYPES and not c.is_primary_key for c in aux_cols):
                return True
        return False

    def _seed_aux_risky_values(
        self,
        conn,
        aux_vs_tables,
        aux_name_map: Dict[str, str],
        relax_profile: RelaxationProfile,
        main_cols: List[ColDef],
        skew: SkewProfile,
    ) -> int:
        if not self._ab.opt4_type_conversion:
            return 0
        seeds = self._build_risky_seed_literals(main_cols, skew, relax_profile)
        if not seeds:
            return 0

        inserted = 0
        row_seed = 0
        for tbl in aux_vs_tables:
            actual_name = aux_name_map[tbl.name]
            aux_cols = self._vs_table_to_coldefs(tbl)
            str_cols = [c for c in aux_cols if c.data_type in _STRING_LIKE_TYPES and not c.is_primary_key]
            if not str_cols:
                continue

            table_seeds = list(seeds)
            random.shuffle(table_seeds)
            for seed_literal in table_seeds[:min(len(table_seeds), AUX_RISKY_SEEDS_PER_TABLE)]:
                target_col = random.choice(str_cols)
                if self._insert_aux_seed_row(
                    conn, actual_name, aux_cols, target_col.name, seed_literal, row_seed
                ):
                    inserted += 1
                row_seed += 1
        return inserted

    def _build_risky_seed_literals(
        self,
        main_cols: List[ColDef],
        skew: SkewProfile,
        relax_profile: RelaxationProfile,
    ) -> List[str]:
        seeds: List[str] = []
        for col in main_cols:
            if col.name not in relax_profile.relaxed_cols:
                continue

            hot_values = skew.hot_values_by_col.get(col.name, [])
            for hot in hot_values[:3]:
                seed_lit = self._string_seed_literal(hot)
                if seed_lit is not None:
                    seeds.append(seed_lit)

            dt = col.data_type
            if dt == 'DATE':
                seeds.extend(["'2023-01-01'", "'2023-01-01 00:00:00'", "'not-a-date'", "'0000-00-00'"])
            elif dt in ('DATETIME', 'TIMESTAMP'):
                seeds.extend(["'2023-01-01 00:00:00'", "'2023-01-01'", "'not-a-date'"])
            elif dt == 'YEAR':
                seeds.extend(["'2023'", "'2023-01-01'", "'023'", "'1999'"])
            elif dt in ('INT', 'FLOAT', 'DOUBLE', 'DECIMAL'):
                seeds.extend(["'0'", "'1'", "'-1'", "'01e0'", "' 1'"])
            elif dt in _STRING_LIKE_TYPES:
                seeds.extend(["''", "'0'", "'01e0'", "'not-a-date'", "'2023-01-01'"])

        deduped: List[str] = []
        seen: Set[str] = set()
        for seed in seeds:
            if seed not in seen:
                deduped.append(seed)
                seen.add(seed)
        return deduped

    def _string_seed_literal(self, literal: str) -> Optional[str]:
        lit = literal.strip()
        if not lit or lit.upper() == 'NULL':
            return None
        if lit.startswith("'") and lit.endswith("'"):
            return lit
        escaped = lit.replace("\\", "\\\\").replace("'", "''")
        return f"'{escaped}'"

    def _insert_aux_seed_row(
        self,
        conn,
        table_name: str,
        cols: List[ColDef],
        target_col_name: str,
        seed_literal: str,
        row_seed: int,
    ) -> bool:
        values: List[str] = []
        for idx, col in enumerate(cols):
            if col.is_primary_key:
                values.append(str(8_000_000 + row_seed))
            elif col.name == target_col_name:
                values.append(seed_literal)
            else:
                values.append(self._safe_literal_for_col(col, row_seed * 31 + idx + 7))

        col_names = ', '.join(f'`{c.name}`' for c in cols)
        sql = f"INSERT IGNORE INTO {table_name} ({col_names}) VALUES ({', '.join(values)})"
        self._sql_log.append(sql + ';')
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return True
        except Exception as e:
            self._log(f"  aux risky seed skipped on {table_name}: {e}")
            return False

    def _collect_fk_valid_pools(
        self,
        conn,
        relax_profile: RelaxationProfile,
    ) -> Dict[str, List[str]]:
        pools: Dict[str, List[str]] = {}
        for main_col, (ref_table, ref_col) in relax_profile.fk_relaxed_map.items():
            values: List[str] = []
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {self._qi(ref_col)} FROM {self._qi(ref_table)}")
                    for row in cur.fetchall():
                        if row and row[0] is not None:
                            values.append(str(row[0]))
            except Exception as e:
                self._log(f"  FK valid-pool collection failed for {main_col}: {e}")
            pools[main_col] = values
        return pools

    def _execute_insert_replay(
        self,
        conn,
        table_name: str,
        cols: List[ColDef],
        values: List[str],
        suppress_expected_rejection_log: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        col_names = ', '.join(f'`{c.name}`' for c in cols)
        sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({', '.join(values)})"
        self._sql_log.append(sql + ';')
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            return True, None
        except Exception as e:
            if not (
                suppress_expected_rejection_log
                and self._is_expected_constraint_rejection(e)
            ):
                self._log(f"  INSERT rejected on {table_name}: {e}")
            return False, str(e)

    def _is_expected_constraint_rejection(self, err: Exception) -> bool:
        code = err.args[0] if getattr(err, 'args', None) else None
        if code in {1048, 1062, 1452, 3819, 4025}:
            return True
        msg = str(err).lower()
        return any(
            token in msg
            for token in (
                'cannot be null',
                'duplicate entry',
                'foreign key constraint fails',
                'check constraint',
                'chk_v_',
                'fk_v_',
            )
        )

    def _verify_table_subset(self, conn, s1_name: str, s2_name: str) -> bool:
        s1_digests = self._capture_row_digests(conn, f"SELECT * FROM {s1_name}")
        s2_digests = self._capture_row_digests(conn, f"SELECT * FROM {s2_name}")
        remaining = dict(s2_digests)
        for digest, cnt in s1_digests.items():
            avail = remaining.get(digest, 0)
            if avail < cnt:
                self._log(
                    f"  subset sanity failed: digest={digest[:12]} missing={cnt - avail}"
                )
                return False
            remaining[digest] = avail - cnt
        self._log(
            f"  Main-table subset sanity passed: |S1|={sum(s1_digests.values())} "
            f"subset |S2|={sum(s2_digests.values())}"
        )
        return True

    def _min_required_s2_only_rows(
        self,
        replay_stats: ReplayStats,
        relax_profile: RelaxationProfile,
    ) -> int:
        floor = MIN_S2_ONLY_ROWS
        if relax_profile.null_relaxed_cols:
            stressed_rows = max(0, INSERT_WORKLOAD_ROWS - BOOTSTRAP_ROWS)
            expected_null_stress = int(stressed_rows * NULL_STRESS_PROB)
            floor = max(floor, expected_null_stress // 5)
        if (
            relax_profile.single_unique_cols
            or relax_profile.composite_unique_groups
            or relax_profile.check_relaxed_cols
            or relax_profile.fk_relaxed_map
        ):
            floor = max(floor, replay_stats.s1_fail // 4)
        return floor

    def _build_vertical_baselines(
        self,
        conn,
        aux_vs_tables,
        s1_name: str,
        aux_name_map: Dict[str, str],
        main_cols: List[ColDef],
        numeric_cols: List[ColDef],
        skew: SkewProfile,
        indexed_cols: Set[str],
        relax_profile: RelaxationProfile,
    ) -> List[Tuple[QuerySpec, QuerySnapshot]]:
        target_baselines = VERTICAL_TARGET_BASELINE_QUERIES
        tables = [(s1_name, main_cols)]
        tables.extend(
            (aux_name_map[tbl.name], self._vs_table_to_coldefs(tbl))
            for tbl in aux_vs_tables
        )

        gen = VerticalQueryGenerator(
            tables=tables,
            skew_hot_values={s1_name: skew.hot_values_by_col},
            dialect=self._dialect,
            enable_known_mysql_date_index_string_eq_workaround=(
                self._enable_known_mysql_date_index_string_eq_workaround
            ),
            main_table_name=s1_name,
            relaxed_cols=relax_profile.relaxed_cols,
            null_relaxed_cols=relax_profile.null_relaxed_cols,
            unique_cols=(
                set(relax_profile.single_unique_cols)
                | {name for group in relax_profile.composite_unique_groups for name in group}
            ),
            check_cols=set(relax_profile.check_relaxed_cols),
            fk_relaxed_map=relax_profile.fk_relaxed_map,
            ablation_config=self._ab,
        )

        results: Dict[str, Tuple[QuerySpec, QuerySnapshot]] = {}
        generator_added = 0
        for _ in range(VERTICAL_QUERY_GEN_ATTEMPTS):
            if len(results) >= target_baselines:
                break

            tag, sql = gen.generate_tagged()
            if not sql or sql in results:
                continue

            # In the vertical oracle, only the main table differs between S1 and S2.
            # Aux-only queries are valid SQL but do not exercise the schema-relaxation relation.
            if s1_name not in sql:
                continue
            if (
                self._ab.opt5_constraint_bias
                and len(results) < RELAXED_BIAS_MIN_QUERIES
                and not self._sql_mentions_relaxed_cols(sql, s1_name, relax_profile.relaxed_cols)
            ):
                continue

            known_bug_reason = self._known_mysql_vertical_bug_reason(conn, sql)
            if known_bug_reason and not self._ab.disable_known_bug_suppression:
                self._log(
                    f"  [vertical_gen:{tag}] skipped known MySQL bug shape: "
                    f"{known_bug_reason}"
                )
                continue

            snap = self._execute_snapshot(conn, sql, numeric_cols)
            if snap is None or not snap.count:
                continue
            results[sql] = (QuerySpec(table_name=s1_name, select_sql=sql), snap)
            generator_added += 1
            self._log(f"  [vertical_gen:{tag}] {sql[:80]}...")

        self._log(
            "  Collected "
            f"{len(results)}/{target_baselines} baseline queries "
            f"(generator={generator_added})."
        )
        return list(results.values())

    def _build_vertical_dml_baselines(
        self,
        conn,
        aux_vs_tables,
        s1_name: str,
        aux_name_map: Dict[str, str],
        main_cols: List[ColDef],
        skew: SkewProfile,
        relax_profile: RelaxationProfile,
    ) -> List[Tuple[DMLEffectSpec, DMLEffectSnapshot]]:
        tables = [(s1_name, main_cols)]
        tables.extend(
            (aux_name_map[tbl.name], self._vs_table_to_coldefs(tbl))
            for tbl in aux_vs_tables
        )
        gen = VerticalQueryGenerator(
            tables=tables,
            skew_hot_values={s1_name: skew.hot_values_by_col},
            dialect=self._dialect,
            enable_known_mysql_date_index_string_eq_workaround=(
                self._enable_known_mysql_date_index_string_eq_workaround
            ),
            main_table_name=s1_name,
            relaxed_cols=relax_profile.relaxed_cols,
            null_relaxed_cols=relax_profile.null_relaxed_cols,
            unique_cols=(
                set(relax_profile.single_unique_cols)
                | {name for group in relax_profile.composite_unique_groups for name in group}
            ),
            check_cols=set(relax_profile.check_relaxed_cols),
            fk_relaxed_map=relax_profile.fk_relaxed_map,
            ablation_config=self._ab,
        )
        return self._build_dml_effect_baselines(conn, gen)

    def _rewrite_vertical_dml_spec(
        self,
        spec: DMLEffectSpec,
        s1_name: str,
        s2_name: str,
    ) -> DMLEffectSpec:
        return DMLEffectSpec(
            table_name=s2_name,
            dml_sql=spec.dml_sql.replace(s1_name, s2_name),
            dml_type=spec.dml_type,
            key_select_sql=spec.key_select_sql.replace(s1_name, s2_name),
            assertion=spec.assertion,
        )

    def _log_vertical_dml_bug(
        self,
        error_msg: str,
        s1_spec: DMLEffectSpec,
        s2_spec: DMLEffectSpec,
        s1: DMLEffectSnapshot,
        s2: DMLEffectSnapshot,
        uid: str,
    ) -> None:
        log_path = os.path.join(
            self._log_dir,
            f'VerticalOracle_bugs_{time.strftime("%Y%m%d_%H%M%S")}.log'
        )
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"[{ts}] Round #{uid} DML BUG DETECTED\n")
            f.write(f"S1 DML       : {s1_spec.dml_sql}\n")
            f.write(f"S2 DML       : {s2_spec.dml_sql}\n")
            f.write(f"DML type     : {s1_spec.dml_type}\n")
            f.write(f"S1 Key SELECT: {s1_spec.key_select_sql}\n")
            f.write(f"S2 Key SELECT: {s2_spec.key_select_sql}\n")
            f.write(f"Plan S1      : {self._fmt_plan(s1.explain_plan)}\n")
            f.write(f"Plan S2      : {self._fmt_plan(s2.explain_plan)}\n")
            f.write(f"S1 rows      : {s1.rows_affected}\n")
            f.write(f"S2 rows      : {s2.rows_affected}\n")
            f.write(f"S1 key count : {sum(s1.affected_keys.values())}\n")
            f.write(f"S2 key count : {sum(s2.affected_keys.values())}\n")
            f.write(f"Error        : {error_msg}\n")
            self._write_repro_sql(
                f,
                verification_sql=self._vertical_dml_verification_sql(s1_spec, s2_spec),
            )
        self._log(f"  [BUG] Logged to {log_path}")
        print(f"[VerticalOracle] DML BUG DETECTED: {error_msg[:120]}")

    def _vertical_dml_verification_sql(
        self,
        s1_spec: DMLEffectSpec,
        s2_spec: DMLEffectSpec,
    ) -> List[str]:
        lines = self._session_diagnostics_sql()
        lines.extend([
            '-- Rollback-only vertical DML probe on S1 and S2.',
            f"SELECT COUNT(*) FROM ({s1_spec.key_select_sql}) AS _s1_probe_before;",
            'START TRANSACTION;',
            s1_spec.key_select_sql + ';',
            s1_spec.dml_sql + ';',
            'ROLLBACK;',
            f"SELECT COUNT(*) FROM ({s2_spec.key_select_sql}) AS _s2_probe_before;",
            'START TRANSACTION;',
            s2_spec.key_select_sql + ';',
            s2_spec.dml_sql + ';',
            'ROLLBACK;',
        ])
        if self._supports_show_warnings():
            lines.append('SHOW WARNINGS;')
        return lines

    def _choose_predicate_col(
        self,
        cols: List[ColDef],
        relax_profile: Optional[RelaxationProfile] = None,
    ) -> ColDef:
        if self._ab.opt5_constraint_bias and relax_profile and relax_profile.relaxed_cols:
            relaxed = [
                c for c in cols
                if not c.is_primary_key
                and c.name in relax_profile.relaxed_cols
                and c.data_type in _INDEXABLE_TYPES
            ]
            preferred_relaxed = [
                c for c in relaxed
                if c.data_type in (_TEMPORAL_TYPES + _STRING_LIKE_TYPES)
            ]
            if preferred_relaxed:
                return random.choice(preferred_relaxed)
            if relaxed:
                return random.choice(relaxed)
        return super()._choose_predicate_col(cols)

    def _sql_mentions_relaxed_cols(
        self,
        sql: str,
        s1_name: str,
        relaxed_cols: Set[str],
    ) -> bool:
        if not relaxed_cols:
            return True
        return any(f'`{col_name}`' in sql for col_name in relaxed_cols)

    def _execute_snapshot(
        self,
        conn,
        sql: str,
        numeric_cols: List[ColDef],
    ) -> Optional[QuerySnapshot]:
        snap = QuerySnapshot()
        try:
            wrap = f"({sql}) AS _w"

            snap.count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {wrap}")
            if snap.count is None:
                return None

            result_numeric = self._result_numeric_cols(conn, sql, numeric_cols)
            for c in result_numeric:
                snap.max_values[c.name] = self._exec_single_float(
                    conn, f"SELECT MAX(`{c.name}`) FROM {wrap}"
                )
                snap.min_values[c.name] = self._exec_single_float(
                    conn, f"SELECT MIN(`{c.name}`) FROM {wrap}"
                )

            if snap.count <= 10000:
                row_digests, capture_ok = self._capture_vertical_row_digests_with_status(
                    conn, sql
                )
                snap.row_digests = row_digests if row_digests is not None else {}
                if capture_ok:
                    self._set_snapshot_consistency_error_if_needed(snap, sql)
                    if self._should_skip_snapshot_on_consistency_error(snap):
                        self._log(
                            "  [WARN] Skipping query due to snapshot inconsistency: "
                            f"{snap.snapshot_consistency_error}"
                        )
                        return None

            snap.explain_plan = self._capture_explain_traditional(conn, sql)
        except IgnorableQueryRuntimeError as e:
            self._log(f"  snapshot skipped: {e}")
            return None
        except ConnectionLevelQueryError:
            raise
        except Exception as e:
            self._log(f"  snapshot failed: {e}")
            return None
        return snap

    def _verify(
        self,
        conn,
        spec: QuerySpec,
        s1: QuerySnapshot,
        s2: QuerySnapshot,
        numeric_cols: List[ColDef],
    ):
        try:
            # Keep the consistency check inside the same assertion pipeline as the
            # normal subset checks so known-plan suppressions still apply.
            self._verify_vertical_snapshot_consistency(s1, 'S1')
            self._verify_vertical_snapshot_consistency(s2, 'S2')
            self._verify_count(spec, s1, s2)
            for c in numeric_cols:
                self._verify_max(spec, c.name, s1, s2)
                self._verify_min(spec, c.name, s1, s2)
            if s1.row_digests:
                self._verify_row_subset(conn, spec, s1, s2)
        except AssertionError as e:
            if self._ab.disable_known_bug_suppression:
                raise
            known_bug_reason = self._known_mysql_vertical_verification_bug_reason(
                conn,
                spec.select_sql,
                s1.explain_plan,
                s2.explain_plan,
                str(e),
            )
            if known_bug_reason:
                self._log(f"  Known MySQL/Percona {known_bug_reason} bug suppressed: {e}")
                return
            raise

    def _verify_vertical_snapshot_consistency(
        self,
        snap: QuerySnapshot,
        label: str,
    ) -> None:
        error = getattr(snap, 'snapshot_consistency_error', None)
        if not error:
            return
        raise AssertionError(
            f"{label} snapshot inconsistency:\n"
            f"  {error}\n"
            f"  Plan: {self._fmt_plan(snap.explain_plan)}"
        )

    def _known_mysql_vertical_verification_bug_reason(
        self,
        conn,
        select_sql: str,
        s1_plan: List[str],
        s2_plan: List[str],
        error_text: str,
    ) -> Optional[str]:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return None
        if self._is_known_mysql_null_contradiction_query(select_sql):
            return 'NULL-contradiction'
        if self._is_known_mysql_year_string_in_subquery_bug(conn, select_sql):
            return 'YEAR-string IN-subquery'
        if self._is_known_mysql_exists_year_materialization_bug(
            conn,
            select_sql,
            s1_plan,
            s2_plan,
        ):
            return 'YEAR-string EXISTS-materialization'
        if self._is_known_mysql_implicit_date_string_join_bug(conn, select_sql):
            return 'DATE-string implicit JOIN index lookup'
        if self._is_known_mysql_cast_string_to_date_join_bug(conn, select_sql):
            return 'CAST(string AS DATE) JOIN index lookup'
        if self._is_known_mysql_vertical_exists_year_snapshot_bug(
            conn,
            select_sql,
            s1_plan,
            s2_plan,
            error_text,
        ):
            return 'YEAR-string EXISTS snapshot inconsistency'
        if self._is_known_mysql_vertical_exists_year_order_by_bug(
            conn,
            select_sql,
            s1_plan,
            s2_plan,
        ):
            return 'YEAR-string EXISTS ORDER BY materialization'
        return None

    def _is_known_mysql_vertical_exists_year_snapshot_bug(
        self,
        conn,
        select_sql: str,
        s1_plan: List[str],
        s2_plan: List[str],
        error_text: str,
    ) -> bool:
        if not self._is_mysql_year_string_exists_shape(conn, select_sql):
            return False
        if not self._is_vertical_snapshot_consistency_error(error_text):
            return False
        return (
            self._plan_has_materialized_exists_lookup(s1_plan)
            or self._plan_has_materialized_exists_lookup(s2_plan)
        )

    def _is_known_mysql_vertical_exists_year_order_by_bug(
        self,
        conn,
        select_sql: str,
        s1_plan: List[str],
        s2_plan: List[str],
    ) -> bool:
        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if ' ORDER BY ' not in upper:
            return False
        if not self._is_mysql_year_string_exists_shape(conn, normalized):
            return False
        return (
            self._plan_has_materialized_exists_lookup(s1_plan)
            or self._plan_has_materialized_exists_lookup(s2_plan)
        )

    def _is_mysql_year_string_exists_shape(self, conn, select_sql: str) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if ' EXISTS (' not in upper or 'SELECT 1' not in upper:
            return False
        if 'CAST(' in upper:
            return False

        parsed = self._extract_exists_type_probe_refs(normalized)
        if parsed is None:
            return False

        outer_table, outer_col, inner_table, inner_col = parsed
        outer_type = self._lookup_column_type(conn, outer_table, outer_col)
        inner_type = self._lookup_column_type(conn, inner_table, inner_col)
        if outer_type is None or inner_type is None:
            return False

        return (
            (self._is_string_like_sql_type(outer_type) and inner_type == 'YEAR')
            or (outer_type == 'YEAR' and self._is_string_like_sql_type(inner_type))
        )

    def _is_vertical_snapshot_consistency_error(self, error_text: str) -> bool:
        normalized = error_text.lower()
        return (
            'snapshot inconsistency' in normalized
            and 'count(*)=' in normalized
            and 'row enumeration returned' in normalized
        )

    def _capture_vertical_row_digests_with_status(
        self,
        conn,
        select_sql: str,
    ) -> Tuple[Optional[Dict[str, int]], bool]:
        return self._capture_row_digests_with_status(conn, select_sql)

    def _known_mysql_vertical_bug_reason(self, conn, sql: str) -> Optional[str]:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return None

        if self._is_known_mysql_null_contradiction_query(sql):
            return 'null_contradiction'
        if self._is_known_mysql_year_string_in_subquery_bug(conn, sql):
            return 'year_string_in_subquery'
        return None

    def _normalize_plan_row(self, row: str) -> str:
        def _normalize_possible_keys(match: re.Match) -> str:
            value = match.group(1).strip()
            if value.lower() == 'null':
                return 'possible_keys=null'
            parts = [p for p in value.split(',') if p.strip()]
            return f"possible_keys={len(parts)}"

        def _normalize_key(match: re.Match) -> str:
            value = match.group(1).strip()
            return 'key=null' if value.lower() == 'null' else 'key=used'

        row = super()._normalize_plan_row(row)
        row = re.sub(r'possible_keys=([^;]+)', _normalize_possible_keys, row)
        row = re.sub(r'key=([^;]+)', _normalize_key, row)
        return row
