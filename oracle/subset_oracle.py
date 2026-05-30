"""
SubsetOracle: 基于数据子集关系 S1 ⊆ S2 的 MySQL 逻辑 Bug 检测 Oracle

核心思路：
  1. 复用 valscope 的 create_sample_tables() 获取 t1/t2/t3 的 schema，
     在 MySQL 里建对应临时表（主表+辅助表）
  2. 主表插入少量偏斜数据 → S1；辅助表插入固定数据（全程不变）
  3. 使用 SubsetQueryGenerator 生成行保留查询，在 S1 上收集快照
  4. 主表再大量插入偏斜数据 + ANALYZE TABLE → S2（S1 ⊆ S2）
  5. 比较 S1/S2 的 EXPLAIN 计划是否变化（计划切换时更容易暴露 bug）
  6. 验证单调性：COUNT / MAX / MIN / 行集合子集

单调性保证：
  辅助表固定不变 + 主表只增不减 → JOIN 结果只增不减 → 单调性依然成立
"""

import os
import re
import random
import hashlib
import uuid
import pymysql
import time
from decimal import Decimal, InvalidOperation
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Set
from datetime import datetime
from pymysql.constants import FIELD_TYPE
from oracle.subset_query_gen import SubsetQueryGenerator
from data_structures.db_dialect import get_current_dialect, set_current_dialect
from ablation.ablation_config import DEFAULT_CONFIG
from ablation.optimizer_trace import collect_considered_plan_count


# ─────────────────────────────────────────────
# 常量配置
# ─────────────────────────────────────────────
TARGET_BASELINE_QUERIES      = 6
MIN_BASELINE_QUERIES         = 3
DML_EFFECT_TARGET_BASELINES  = 3
MAX_QUERY_GEN_ATTEMPTS       = 120
BASELINE_HOT_ROWS            = 2
BASELINE_RANDOM_ROWS         = 4
BASELINE_NOISE_ROWS          = 4
SKEWED_EXPANSION_ROWS        = 500
UNCHANGED_PLAN_VERIFY_PROB   = 0.15
FLOAT_TOLERANCE              = 1e-9
EXPECTED_MYSQL_RUNTIME_ERROR_CODES = {1365, 1690}
_RUNTIME_ERROR_CODES: dict = {
    'mysql':    {1365, 1690},
    'mariadb':  {1365, 1690, 1292},   # MariaDB 额外抛 1292（数据截断）
    'percona':  {1365, 1690},
    'monetdb':  set(),
    'tidb':     {1365, 1690, 1105},
    'oceanbase': {1365, 1690, 4016},
    'polardb':  {1365, 1690, 4006},
}
_RUNTIME_ERROR_PATTERNS: dict = {
    'mysql':    ('double value is out of range', 'bigint value is out of range',
                 'decimal value is out of range', 'division by 0', 'division by zero'),
    'mariadb':  ('double value is out of range', 'bigint value is out of range',
                 'decimal value is out of range', 'division by 0', 'division by zero',
                 'incorrect datetime value', 'data too long'),
    'percona':  ('double value is out of range', 'bigint value is out of range',
                 'decimal value is out of range', 'division by 0', 'division by zero'),
    'monetdb':  ('math exception', 'numerical result out of range', 'division by zero'),
    'tidb':     ('value is out of range', 'division by zero',
                 'out of range value', 'data too long'),
    'oceanbase': ('value is out of range', 'division by zero',
                  'numeric value out of range'),
    'polardb':  ('double value is out of range', 'bigint value is out of range',
                 'decimal value is out of range', 'division by 0'),
}

_TEMPORAL_TYPES    = ('DATE', 'DATETIME', 'TIMESTAMP', 'TIME', 'YEAR')
_STRING_LIKE_TYPES = ('VARCHAR', 'TEXT', 'LONGTEXT', 'CHAR', 'ENUM', 'SET')
_INDEXABLE_TYPES   = ('INT', 'VARCHAR', 'TEXT', 'LONGTEXT', 'CHAR', 'ENUM', 'SET') + _TEMPORAL_TYPES
_MYSQL_NUMERIC_FIELD_TYPES = {
    FIELD_TYPE.TINY,
    FIELD_TYPE.SHORT,
    FIELD_TYPE.LONG,
    FIELD_TYPE.LONGLONG,
    FIELD_TYPE.INT24,
    FIELD_TYPE.DECIMAL,
    FIELD_TYPE.NEWDECIMAL,
    FIELD_TYPE.FLOAT,
    FIELD_TYPE.DOUBLE,
    FIELD_TYPE.YEAR,
}
_GENERIC_NUMERIC_TYPE_NAMES = {
    'tinyint', 'smallint', 'int', 'integer', 'mediumint', 'bigint', 'hugeint',
    'serial', 'bigserial',
    'decimal', 'numeric', 'real', 'float', 'double', 'double precision',
}

# ─────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────
@dataclass
class ColDef:
    name: str
    data_type: str       # 'INT' / 'VARCHAR' / 'TEXT' / 'LONGTEXT' / 'DATE' / ...
    declared_type: str   = ''
    is_primary_key: bool = False
    is_nullable: bool    = True
    varchar_len: int     = 128
    is_indexed: bool     = False


@dataclass
class SkewProfile:
    predicate_col: ColDef
    primary_hot:   str
    secondary_hot: str
    tertiary_hot:  str
    expansion_hot: str
    hot_values_by_col: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class QuerySpec:
    table_name: str   # 主表名（用于日志和错误信息）
    select_sql: str   # 完整 SELECT 语句


@dataclass
class QuerySnapshot:
    count:        Optional[int]                  = None
    max_values:   Dict[str, Optional[float]]     = field(default_factory=dict)
    min_values:   Dict[str, Optional[float]]     = field(default_factory=dict)
    row_digests:  Dict[str, int]                 = field(default_factory=dict)
    explain_plan: List[str]                      = field(default_factory=list)
    snapshot_consistency_error: Optional[str]    = None


@dataclass
class DMLEffectSpec:
    table_name: str
    dml_sql: str
    dml_type: str
    key_select_sql: str
    assertion: str = '<='


@dataclass
class DMLEffectSnapshot:
    rows_affected: Optional[int] = None
    affected_keys: Dict[str, int] = field(default_factory=dict)
    explain_plan: List[str] = field(default_factory=list)


class IgnorableQueryRuntimeError(Exception):
    pass


class ConnectionLevelQueryError(Exception):
    pass


_CONNECTION_LEVEL_ERROR_PATTERNS = (
    'packet sequence number wrong',
    "codec can't decode",
    'lost connection',
    'connection was killed',
    'server has gone away',
)


# ═══════════════════════════════════════════════════════════════
class SubsetOracle:

    def __init__(self, db_config: dict, verbose: bool = True,
                 log_sql: bool = False, log_file: str = None,
                 enable_known_mysql_date_index_string_eq_workaround: Optional[bool] = None,
                 ablation_config=None):
        self.db_config = db_config
        self.verbose   = verbose
        self.log_sql   = log_sql
        self.log_file  = log_file
        self.enable_known_mysql_date_index_string_eq_workaround = (
            enable_known_mysql_date_index_string_eq_workaround
        )
        self._ab = ablation_config or DEFAULT_CONFIG
        self._log_dir  = None
        self._sql_log  = []

        self.total_rounds       = 0
        self.total_queries      = 0
        self.total_plan_changes = 0
        self.total_select_queries = 0
        self.total_dml_queries = 0
        self.total_select_plan_changes = 0
        self.total_dml_plan_changes = 0
        self.total_bugs         = 0

    # ──────────────────────────────────────────
    # 公共入口
    # ──────────────────────────────────────────
    def run(self) -> dict:
        uid             = uuid.uuid4().hex[:8]
        self._sql_log = []

        db_type = self.db_config.get('db_type', 'MYSQL').upper()
        set_current_dialect(db_type)          # 确保全局状态与本 round 一致
        self._dialect = get_current_dialect() # 存入实例，后续方法可用

        # 按方言选取运行时错误的判断表
        family = self._dialect.optimizer_family()   # 'mysql' / 'mariadb' / 'percona'
        self._ignorable_codes    = _RUNTIME_ERROR_CODES.get(family,    _RUNTIME_ERROR_CODES['mysql'])
        self._ignorable_patterns = _RUNTIME_ERROR_PATTERNS.get(family, _RUNTIME_ERROR_PATTERNS['mysql'])
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
            'round_id': uid, 'queries': 0,
            'plan_changes': 0, 'bugs': 0, 'skipped': False,
            'select_queries': 0,
            'dml_queries': 0,
            'select_plan_changes': 0,
            'dml_plan_changes': 0,
            'select_baselines': 0,
            'dml_baselines': 0,
            'trace_attempts': 0,
            'trace_successes': 0,
            'trace_truncations': 0,
            'total_considered_plans': 0,
        }

        db_type = self.db_config.get('db_type', 'MYSQL').upper()
        self._log_dir = os.path.join('invalid_mutation', db_type)
        os.makedirs(self._log_dir, exist_ok=True)

        self._log(f"\n{'='*60}")
        self._log(f" SUBSET ORACLE round #{uid}")
        self._log(f"{'='*60}")
        self._log(
            " Known MySQL DATE-index/string-eq workaround: "
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

        name_map, vs_tables, main_vs_table = self._build_name_map(uid)
        main_name       = name_map[main_vs_table.name]
        all_actual_names = list(name_map.values())

        try:
            # ── Step 1：建所有表 ──────────────────────────────
            self._log(f"\n[Step 1] Creating tables ...")
            self._create_all_tables(conn, vs_tables, name_map)

            main_cols    = self._vs_table_to_coldefs(main_vs_table)
            numeric_cols = [c for c in main_cols
                            if c.data_type in ('INT', 'FLOAT', 'DOUBLE', 'DECIMAL')]

            # ── Step 1.5：谓词列、索引、偏斜配置 ─────────────
            pred_col = self._choose_predicate_col(main_cols)
            indexed_cols = self._ensure_indexes(conn, main_name, main_cols, pred_col, uid)
            for c in main_cols:
                c.is_indexed = c.name in indexed_cols
            skew = self._create_skew_profile(main_cols, pred_col)
            self._commit_setup_barrier(conn)
            self._log(f"  Main table: {main_name}, predicate col: {pred_col.name}, "
                      f"primary_hot={skew.primary_hot}")

            # ── Step 2：插入 S1 数据 ──────────────────────────
            self._log(f"\n[Step 2] Building S1 ...")
            self._begin_setup_transaction(conn)
            if self._ab.opt1_data_distribution:
                self._insert_hot_seed_rows(conn, main_name, main_cols, skew, BASELINE_HOT_ROWS)
                self._insert_skewed_rows(conn, main_name, main_cols, skew,
                                         BASELINE_RANDOM_ROWS + random.randint(0, 3),
                                         0.35, stage='baseline')
                self._insert_noise_rows(conn, main_name, main_cols, BASELINE_NOISE_ROWS)
            else:
                self._insert_random_rows(
                    conn,
                    main_name,
                    main_cols,
                    BASELINE_HOT_ROWS + BASELINE_RANDOM_ROWS + BASELINE_NOISE_ROWS + random.randint(0, 3),
                )
            self._insert_aux_data(conn, vs_tables, name_map)
            self._commit_setup_barrier(conn)

            s1_count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {main_name}")
            if not s1_count:
                self._log("  [SKIP] S1 is empty.")
                round_stats['skipped'] = True
                return round_stats
            self._log(f"  S1 row count: {s1_count}")

            # ── Step 3：生成查询并收集 S1 快照 ───────────────
            self._log(f"\n[Step 3] Building baseline queries on S1 ...")
            baselines, dml_baselines = self._build_baselines(
                conn, vs_tables, name_map, main_name, main_cols, numeric_cols, skew,
                indexed_cols_by_table={main_name: indexed_cols})
            round_stats['select_baselines'] = len(baselines)
            round_stats['dml_baselines'] = len(dml_baselines)
            self._log(f"  Validated SELECT baselines: {len(baselines)}")
            self._log(f"  Validated DML-effect baselines: {len(dml_baselines)}")
            if len(baselines) < self._minimum_baseline_queries():
                self._log("  [SKIP] Not enough valid baseline queries.")
                round_stats['skipped'] = True
                return round_stats

            # ── Step 4：插入 S2 数据 + ANALYZE ───────────────
            self._log(f"\n[Step 4] Expanding to S2 ...")
            if not self._is_monetdb_family():
                self._sql_log.append('START TRANSACTION;')
            else:
                self._sql_log.append(
                    '-- MonetDB: end of S1; start of S2 expansion '
                    '(implicit transaction boundary)'
                )
            self._begin_transaction(conn)
            if self._ab.opt1_data_distribution:
                self._insert_skewed_rows(conn, main_name, main_cols, skew,
                                         SKEWED_EXPANSION_ROWS + 64 * random.randint(0, 8),
                                         0.92, stage='expansion')
            else:
                self._insert_random_rows(
                    conn,
                    main_name,
                    main_cols,
                    SKEWED_EXPANSION_ROWS + 64 * random.randint(0, 8),
                )
            self._commit_transaction(conn)
            self._sql_log.append('COMMIT;')

            s2_count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {main_name}")
            if s2_count is None or s2_count <= s1_count:
                self._log("  [SKIP] Table did not grow enough.")
                return round_stats
            self._log(f"  S2 row count: {s2_count}, growth ratio: {s2_count/s1_count:.2f}x")

            self._analyze_table(conn, main_name)

            # ── Step 5+6：验证单调性 ──────────────────────────
            self._log(f"\n[Step 5+6] Verifying monotonicity on S2 ...")
            unchanged_plan_verify_prob = self._dialect.unchanged_plan_verify_prob()
            for i, (spec, s1_snap) in enumerate(baselines):
                s2_plan      = self._capture_explain_traditional(conn, spec.select_sql)
                plan_changed = not self._plans_equivalent(s1_snap.explain_plan, s2_plan)
                self._log(f"  Query[{i+1}] plan_changed={plan_changed}")
                self._log(f"  Query[{i+1}] SQL: {spec.select_sql}")
                self._log(f"  Query[{i+1}] Plan S1: {self._fmt_plan(s1_snap.explain_plan)}")
                self._log(f"  Query[{i+1}] Plan S2: {self._fmt_plan(s2_plan)}")

                effective_prob = (
                    unchanged_plan_verify_prob if self._ab.opt3_plan_filter else 1.0
                )
                if not plan_changed and random.random() > effective_prob:
                    self._log(f"  Query[{i+1}] plan unchanged, skipping.")
                    continue

                # S2 快照：与 S1 使用完全相同的方法，保证可比性
                s2_snap = self._execute_snapshot(conn, spec.select_sql, numeric_cols)
                if s2_snap is None:
                    self._log(f"  Query[{i+1}] skipped due to expected query runtime error.")
                    continue
                s2_snap.explain_plan = s2_plan
                self._collect_trace_stats(conn, spec.select_sql, round_stats)

                try:
                    self._verify(conn, spec, s1_snap, s2_snap, numeric_cols)
                    round_stats['queries'] += 1
                    round_stats['select_queries'] += 1
                    if plan_changed:
                        round_stats['plan_changes'] += 1
                        round_stats['select_plan_changes'] += 1
                except IgnorableQueryRuntimeError as e:
                    self._log(f"  Query[{i+1}] skipped during verification: {e}")
                except AssertionError as e:
                    round_stats['queries'] += 1
                    round_stats['select_queries'] += 1
                    if plan_changed:
                        round_stats['plan_changes'] += 1
                        round_stats['select_plan_changes'] += 1
                    round_stats['bugs'] += 1
                    self._log_bug(str(e), spec, s1_snap, s2_snap, uid)

            for i, (spec, s1_snap) in enumerate(dml_baselines):
                s2_plan = self._capture_explain_dml(conn, spec.dml_sql)
                plan_changed = not self._plans_equivalent(s1_snap.explain_plan, s2_plan)
                self._log(f"  DML[{i+1}] plan_changed={plan_changed}")
                self._log(f"  DML[{i+1}] SQL: {spec.dml_sql}")
                self._log(f"  DML[{i+1}] Plan S1: {self._fmt_plan(s1_snap.explain_plan)}")
                self._log(f"  DML[{i+1}] Plan S2: {self._fmt_plan(s2_plan)}")

                effective_prob = (
                    unchanged_plan_verify_prob if self._ab.opt3_plan_filter else 1.0
                )
                if not plan_changed and random.random() > effective_prob:
                    self._log(f"  DML[{i+1}] plan unchanged, skipping.")
                    continue

                s2_snap = self._execute_dml_effect_snapshot(conn, spec)
                if s2_snap is None:
                    self._log(f"  DML[{i+1}] skipped due to expected query runtime error.")
                    continue
                s2_snap.explain_plan = s2_plan
                self._collect_trace_stats(conn, spec.dml_sql, round_stats)

                try:
                    self._verify_dml_effect(spec, s1_snap, s2_snap)
                    round_stats['queries'] += 1
                    round_stats['dml_queries'] += 1
                    if plan_changed:
                        round_stats['plan_changes'] += 1
                        round_stats['dml_plan_changes'] += 1
                except AssertionError as e:
                    round_stats['queries'] += 1
                    round_stats['dml_queries'] += 1
                    if plan_changed:
                        round_stats['plan_changes'] += 1
                        round_stats['dml_plan_changes'] += 1
                    round_stats['bugs'] += 1
                    self._log_dml_bug(str(e), spec, s1_snap, s2_snap, uid)

            if round_stats['bugs'] == 0:
                self._log(f"\n  All checks PASSED for round #{uid}")
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
            cleanup_conn = conn
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                cleanup_conn = self._connect()

            if cleanup_conn is not None:
                for n in all_actual_names:
                    self._drop_if_exists(cleanup_conn, n)
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
            self._log(f"{'='*60}\n")

        if not round_stats['skipped']:
            self.total_rounds       += 1
            self.total_queries      += round_stats['queries']
            self.total_plan_changes += round_stats['plan_changes']
            self.total_select_queries += round_stats['select_queries']
            self.total_dml_queries += round_stats['dml_queries']
            self.total_select_plan_changes += round_stats['select_plan_changes']
            self.total_dml_plan_changes += round_stats['dml_plan_changes']
            self.total_bugs         += round_stats['bugs']

        return round_stats

    # ──────────────────────────────────────────
    # valscope 集成：表结构
    # ──────────────────────────────────────────
    def _build_name_map(self, uid: str):
        from generate_random_sql import create_sample_tables
        vs_tables = create_sample_tables()
        name_map  = {}
        for i, tbl in enumerate(vs_tables):
            if i == 0:
                name_map[tbl.name] = f"subset3_{uid}"
            else:
                name_map[tbl.name] = f"subset3_ref_{uid}_{tbl.name}"
        return name_map, vs_tables, vs_tables[0]

    def _create_all_tables(self, conn, vs_tables, name_map: dict):
        from generate_random_sql import generate_create_table_sql
        for tbl in vs_tables:
            ddl = generate_create_table_sql(tbl)
            ddl = ddl.replace(f"CREATE TABLE {tbl.name}",
                            f"CREATE TABLE {name_map[tbl.name]}")
            ddl = self._strip_foreign_keys(ddl)
            # MariaDB 不支持原生 JSON，替换为 LONGTEXT
            ddl = self._normalize_ddl_for_dialect(ddl)
            self._exec_ddl(conn, ddl)
            self._log(f"  Created: {name_map[tbl.name]}")

    def _normalize_ddl_for_dialect(self, ddl: str) -> str:
        if not self._dialect.supports_json_type():
            ddl = re.sub(r'\bJSON\b', 'LONGTEXT', ddl, flags=re.IGNORECASE)
        return ddl

    def _strip_foreign_keys(self, ddl: str) -> str:
        lines   = ddl.split('\n')
        cleaned = [l for l in lines
                   if 'FOREIGN KEY' not in l.upper() and 'REFERENCES' not in l.upper()]
        result  = '\n'.join(cleaned)
        return re.sub(r',\s*\n(\s*\))', r'\n\1', result)

    def _declared_length(self, dt: str, default: int) -> int:
        match = re.search(r'\((\d+)\)', dt)
        if not match:
            return default
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return default

    def _vs_table_to_coldefs(self, vs_table, indexed_col_names: Optional[Set[str]] = None) -> List[ColDef]:
        indexed_col_names = indexed_col_names or set()
        cols = []
        for c in vs_table.columns:
            dt = c.data_type.upper()
            if dt.startswith('VARCHAR'):
                base_dt, vlen = 'VARCHAR', self._declared_length(dt, 255)
            elif dt.startswith('CHAR'):
                base_dt, vlen = 'CHAR', self._declared_length(dt, 255)
            elif dt.startswith('ENUM'):
                base_dt, vlen = 'ENUM', 255
            elif dt.startswith('SET('):
                base_dt, vlen = 'SET', 255
            elif 'LONGTEXT' in dt or 'MEDIUMTEXT' in dt:
                base_dt, vlen = 'LONGTEXT', 1024
            elif 'TEXT' in dt:
                base_dt, vlen = 'TEXT', 512
            elif dt.startswith('DECIMAL') or dt.startswith('NUMERIC'):
                base_dt, vlen = 'DECIMAL', 128
            elif 'FLOAT' in dt:
                base_dt, vlen = 'FLOAT', 128
            elif 'DOUBLE' in dt:
                base_dt, vlen = 'DOUBLE', 128
            elif dt.startswith('DATETIME'):
                base_dt, vlen = 'DATETIME', 32
            elif dt.startswith('TIMESTAMP'):
                base_dt, vlen = 'TIMESTAMP', 32
            elif dt == 'DATE':
                base_dt, vlen = 'DATE', 32
            elif dt.startswith('TIME'):
                base_dt, vlen = 'TIME', 32
            elif dt == 'YEAR':
                base_dt, vlen = 'YEAR', 32
            elif dt.startswith('SMALLINT') or dt.startswith('TINYINT') or dt.startswith('MEDIUMINT') or dt.startswith('BIGINT'):
                base_dt, vlen = 'INT', 128
            elif 'BLOB' in dt or 'BINARY' in dt or 'BIT' in dt \
                    or 'JSON' in dt or 'GEOMETRY' in dt or 'POINT' in dt \
                    or 'POLYGON' in dt or 'LINESTRING' in dt:
                base_dt = 'OPAQUE'  # 独立类型：禁止参与 JOIN / 列间比较，只生成 IS NULL / IS NOT NULL
                vlen = 128
            else:
                base_dt, vlen = 'INT', 128
            cols.append(ColDef(
                name=c.name,
                data_type=base_dt,
                declared_type=c.data_type,
                is_primary_key=(c.name == vs_table.primary_key),
                is_nullable=c.is_nullable,
                varchar_len=vlen,
                is_indexed=(c.name in indexed_col_names),
            ))
        return cols

    def _insert_aux_data(self, conn, vs_tables, name_map: dict):
        from generate_random_sql import generate_insert_sql
        primary_keys_dict = {tbl.name: list(range(1, 21)) for tbl in vs_tables}
        for tbl in vs_tables[1:]:
            actual = name_map[tbl.name]
            coldefs = self._vs_table_to_coldefs(tbl)
            try:
                insert_sql = generate_insert_sql(
                    tbl, num_rows=10,
                    existing_primary_keys=primary_keys_dict,
                    primary_key_values=list(range(1, 11))
                )
                for line in insert_sql.strip().split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    line = self._rewrite_generated_insert(line, tbl.name, actual)
                    err = self._execute_best_effort_dml(conn, line.rstrip(';'))
                    if err is not None:
                        self._log(f"  aux insert skipped: {err}")
                self._insert_aux_coercion_rows(conn, actual, coldefs)
            except Exception as e:
                self._rollback_after_statement_error(conn)
                self._log(f"  aux data gen failed for {actual}: {e}")

    # ──────────────────────────────────────────
    # Step 3：查询生成 + S1 快照
    # ──────────────────────────────────────────
    def _build_baselines(self, conn, vs_tables, name_map: dict,
                          main_name: str, main_cols: List[ColDef],
                          numeric_cols: List[ColDef],
                          skew: SkewProfile,
                          indexed_cols_by_table: Optional[Dict[str, Set[str]]] = None
                          ) -> Tuple[
                              List[Tuple[QuerySpec, QuerySnapshot]],
                              List[Tuple[DMLEffectSpec, DMLEffectSnapshot]],
                          ]:
        indexed_cols_by_table = indexed_cols_by_table or {}
        tables = [
            (
                name_map[tbl.name],
                self._vs_table_to_coldefs(
                    tbl,
                    indexed_col_names=indexed_cols_by_table.get(name_map[tbl.name], set()),
                ),
            )
            for tbl in vs_tables
        ]
        gen = SubsetQueryGenerator(
            tables=tables,
            skew_hot_values={name_map[vs_tables[0].name]: skew.hot_values_by_col},
            dialect=self._dialect,
            enable_known_mysql_date_index_string_eq_workaround=(
                self._enable_known_mysql_date_index_string_eq_workaround
            ),
            main_table_name=main_name,
            ablation_config=self._ab,
        )

        select_results = self._build_select_baselines(
            conn=conn,
            gen=gen,
            vs_tables=vs_tables,
            main_name=main_name,
            numeric_cols=numeric_cols,
        )
        dml_results = self._build_dml_effect_baselines(conn, gen)
        return select_results, dml_results

    def _build_select_baselines(
        self,
        conn,
        gen: SubsetQueryGenerator,
        vs_tables,
        main_name: str,
        numeric_cols: List[ColDef],
    ) -> List[Tuple[QuerySpec, QuerySnapshot]]:
        results: Dict[str, Tuple[QuerySpec, QuerySnapshot]] = {}
        target_baseline_queries = self._target_baseline_queries()
        min_main_table_queries = self._min_main_table_queries(target_baseline_queries)
        min_risky_queries = self._min_risky_baseline_queries(vs_tables)
        for _ in range(self._max_query_gen_attempts()):
            risky_count = sum(
                1 for s in results
                if 'implicit_conversion_' in s.lower() or 'rare_' in s.lower()
            )
            if len(results) >= target_baseline_queries and risky_count >= min_risky_queries:
                break
            sql = gen.generate()
            if not sql or sql in results:
                continue

            # 如果主表查询还不够，跳过不涉及主表的 SQL
            main_table_count = sum(1 for s in results if main_name in s)
            if main_table_count < min_main_table_queries and main_name not in sql:
                continue

            snap = self._execute_snapshot(conn, sql, numeric_cols)
            if snap is None or not snap.count:
                continue
            results[sql] = (QuerySpec(table_name=main_name, select_sql=sql), snap)
            self._log(f"  [query_gen] {sql[:80]}...")

        self._log(f"  Collected {len(results)}/{target_baseline_queries} SELECT baselines.")
        return list(results.values())

    def _build_dml_effect_baselines(
        self,
        conn,
        gen: SubsetQueryGenerator,
        target: int = DML_EFFECT_TARGET_BASELINES,
    ) -> List[Tuple[DMLEffectSpec, DMLEffectSnapshot]]:
        results: List[Tuple[DMLEffectSpec, DMLEffectSnapshot]] = []
        seen_sql: Set[str] = set()
        for _ in range(max(target * 8, 12)):
            if len(results) >= target:
                break
            spec = gen.generate_dml_effect()
            if spec is None or spec.dml_sql in seen_sql:
                continue
            snap = self._execute_dml_effect_snapshot(conn, spec)
            if (
                snap is None
                or snap.rows_affected is None
                or not snap.affected_keys
            ):
                continue
            seen_sql.add(spec.dml_sql)
            results.append((spec, snap))
            self._log(
                f"  [dml_effect] {spec.dml_type} rows={snap.rows_affected} "
                f"keys={sum(snap.affected_keys.values())} {spec.dml_sql[:80]}..."
            )

        self._log(f"  Collected {len(results)}/{target} DML-effect baselines.")
        return results

    # ──────────────────────────────────────────
    # 快照执行（S1 和 S2 共用同一方法）
    # ──────────────────────────────────────────
    def _execute_snapshot(self, conn, sql: str,
                        numeric_cols: List[ColDef]) -> Optional[QuerySnapshot]:
        snap = QuerySnapshot()
        try:
            wrap = f"({sql}) AS _w"

            snap.count = self._exec_single_int(conn, f"SELECT COUNT(*) FROM {wrap}")
            if snap.count is None:
                return None   # COUNT 失败才早退出，不影响其他查询

            result_numeric = self._result_numeric_cols(conn, sql, numeric_cols)
            for c in result_numeric:
                snap.max_values[c.name] = self._exec_single_float(
                    conn, f"SELECT MAX({self._qi(c.name)}) FROM {wrap}")
                snap.min_values[c.name] = self._exec_single_float(
                    conn, f"SELECT MIN({self._qi(c.name)}) FROM {wrap}")

            if snap.count <= 10000:
                row_digests, capture_ok = self._capture_row_digests_with_status(conn, sql)
                if not capture_ok:
                    self._log(
                        "  [WARN] Skipping query because row materialization failed "
                        "after COUNT(*) succeeded."
                    )
                    return None
                snap.row_digests = row_digests if row_digests is not None else {}
                self._set_snapshot_consistency_error_if_needed(snap, sql)
                if self._should_skip_snapshot_on_consistency_error(snap):
                    self._log(
                        "  [WARN] Skipping query due to snapshot inconsistency: "
                        f"{snap.snapshot_consistency_error}"
                    )
                    return None

            snap.explain_plan = self._capture_explain_traditional(conn, sql)
        except IgnorableQueryRuntimeError as e:
            self._rollback_after_statement_error(conn)
            self._log(f"  snapshot skipped: {e}")
            return None
        except ConnectionLevelQueryError:
            raise
        except Exception as e:
            self._rollback_after_statement_error(conn)
            self._log(f"  snapshot failed: {e}")
            return None
        return snap

    def _execute_dml_effect_snapshot(
        self,
        conn,
        spec: DMLEffectSpec,
    ) -> Optional[DMLEffectSnapshot]:
        snap = DMLEffectSnapshot()
        tx_started = False
        try:
            snap.explain_plan = self._capture_explain_dml(conn, spec.dml_sql)
            if not self._is_monetdb_family():
                self._sql_log.append('START TRANSACTION;')
            else:
                self._sql_log.append(
                    '-- MonetDB: start DML-effect probe transaction'
                )
            self._begin_transaction(conn)
            tx_started = True

            affected_keys, capture_ok = self._capture_row_digests_with_status(
                conn,
                spec.key_select_sql,
            )
            if not capture_ok:
                return None
            snap.affected_keys = affected_keys or {}

            self._sql_log.append(spec.dml_sql + ';')
            with conn.cursor() as cur:
                cur.execute(spec.dml_sql)
                # Read rowcount before rollback because some drivers reset it.
                snap.rows_affected = cur.rowcount
        except IgnorableQueryRuntimeError as e:
            self._log(f"  dml_effect snapshot skipped: {e}")
            return None
        except ConnectionLevelQueryError:
            raise
        except Exception as e:
            self._raise_if_connection_level_error(e)
            if self._is_expected_query_runtime_error(e):
                self._log(f"  dml_effect snapshot skipped: {e}")
                return None
            self._log(f"  dml_effect snapshot failed: {e}")
            return None
        finally:
            if tx_started:
                self._rollback_transaction(conn)
                self._sql_log.append('ROLLBACK;')
        return snap

    def _capture_explain_dml(self, conn, sql: str) -> List[str]:
        return self._capture_explain_traditional(conn, sql)

    def _collect_trace_stats(self, conn, sql: str, round_stats: Dict[str, int]) -> None:
        if not self._ab.enable_trace:
            return
        trace_stats = collect_considered_plan_count(conn, sql)
        round_stats['trace_attempts'] += trace_stats['attempted']
        round_stats['trace_successes'] += trace_stats['success']
        round_stats['trace_truncations'] += trace_stats['truncated']
        round_stats['total_considered_plans'] += trace_stats['considered_plans']

    def _result_numeric_cols(self, conn, sql: str,
                            numeric_cols: List[ColDef]) -> List[ColDef]:
        """查询实际暴露的列名，过滤掉不在 SELECT 列表里的 numeric_cols。"""
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM ({sql}) AS _sc LIMIT 0")
                desc = list(cur.description or [])

            if not desc:
                return []

            name_counts = Counter(d[0] for d in desc)
            numeric_result_names = {
                d[0] for d in desc
                if name_counts[d[0]] == 1 and self._column_type_code_is_numeric(d[1])
            }
            return [c for c in numeric_cols if c.name in numeric_result_names]
        except ConnectionLevelQueryError:
            raise
        except Exception as e:
            self._raise_if_connection_level_error(e)
            self._rollback_after_statement_error(conn)
            return []


    # ──────────────────────────────────────────
    # Step 1.5：谓词列 / 索引 / 偏斜配置
    # ──────────────────────────────────────────
    def _choose_predicate_col(self, cols: List[ColDef]) -> ColDef:
        preferred = [c for c in cols if not c.is_primary_key
                     and c.data_type in _TEMPORAL_TYPES + _STRING_LIKE_TYPES]
        if preferred and random.random() < 0.75:
            return random.choice(preferred)
        preferred = [c for c in cols if not c.is_primary_key
                     and c.data_type in ('INT', 'FLOAT', 'DOUBLE', 'DECIMAL')]
        if preferred:
            return random.choice(preferred)
        non_pk = [c for c in cols if not c.is_primary_key]
        return random.choice(non_pk) if non_pk else cols[0]

    def _ensure_indexes(self, conn, table_name: str, cols: List[ColDef],
                        pred_col: ColDef, uid: str) -> Set[str]:
        if not self._ab.opt2_index_and_analyze:
            self._log("  [ablation] opt2 off: skipping index creation")
            return set()
        indexed_cols: Set[str] = set()
        if self._col_is_indexable(pred_col):
            pred_idx_col = self._index_expr(pred_col)
            self._exec_ddl(conn, f"CREATE INDEX i_s3_{uid} ON {table_name} ({pred_idx_col})",
                           ignore_error=True)
            indexed_cols.add(pred_col.name)

        candidates = [
            c for c in cols
            if c.name != pred_col.name
            and not c.is_primary_key
            and self._col_is_indexable(c)
        ]

        if candidates:
            extra_cols = random.sample(candidates, k=min(random.randint(1, 2), len(candidates)))
            for c in extra_cols:
                self._exec_ddl(
                    conn,
                    f"CREATE INDEX i_s3_{uid}_{c.name} ON {table_name} ({self._index_expr(c)})",
                    ignore_error=True,
                )
                indexed_cols.add(c.name)

        comp_candidates = [
            c for c in cols
            if not c.is_primary_key and self._col_is_indexable(c)
        ]
        pred_eligible = self._col_is_indexable(pred_col) and not pred_col.is_primary_key
        if pred_eligible and len(comp_candidates) >= 2 and random.random() < 0.8:
            others = [c for c in comp_candidates if c.name != pred_col.name]
            if others:
                c2 = random.choice(others)
                self._exec_ddl(
                    conn,
                    f"CREATE INDEX i_s3_{uid}_comp ON {table_name} "
                    f"({self._index_expr(pred_col)}, {self._index_expr(c2)})",
                    ignore_error=True,
                )
                indexed_cols.add(pred_col.name)
                indexed_cols.add(c2.name)
        elif len(comp_candidates) >= 2 and random.random() < 0.4:
            c1, c2 = random.sample(comp_candidates, k=2)
            self._exec_ddl(
                conn,
                f"CREATE INDEX i_s3_{uid}_comp ON {table_name} "
                f"({self._index_expr(c1)}, {self._index_expr(c2)})",
                ignore_error=True,
            )
            indexed_cols.add(c1.name)
            indexed_cols.add(c2.name)
        return indexed_cols

    def _col_is_indexable(self, col: ColDef) -> bool:
        if col.data_type not in _INDEXABLE_TYPES:
            return False
        if col.data_type in ('TEXT', 'LONGTEXT'):
            return self._dialect.supports_prefix_index_on_text()
        return True

    def _index_expr(self, col: ColDef) -> str:
        if col.data_type in ('TEXT', 'LONGTEXT'):
            max_prefix = 32 if col.data_type == 'LONGTEXT' else 64
            prefix_len = min(max(1, col.varchar_len), max_prefix)
            return f"{self._qi(col.name)}({prefix_len})"
        if col.data_type in _STRING_LIKE_TYPES:
            if self._dialect.supports_prefix_index_on_varchar():
                prefix_len = min(max(1, col.varchar_len), 64)
                return f"{self._qi(col.name)}({prefix_len})"
            return self._qi(col.name)
        return self._qi(col.name)

    def _create_skew_profile(self, cols: List[ColDef], pred_col: ColDef) -> SkewProfile:
        hot_by_col: Dict[str, List[str]] = {c.name: self._create_hot_values(c) for c in cols}
        pred_hots = hot_by_col[pred_col.name]
        primary   = pred_hots[0]
        secondary = pred_hots[1] if len(pred_hots) > 1 else primary
        tertiary  = pred_hots[2] if len(pred_hots) > 2 else secondary
        expansion = (
            self._create_expansion_hot_value(pred_col, pred_hots)
            if self._ab.opt1_data_distribution
            else self._generate_value(pred_col, None, 0.0, 'random')
        )
        return SkewProfile(
            predicate_col=pred_col,
            primary_hot=primary, secondary_hot=secondary,
            tertiary_hot=tertiary, expansion_hot=expansion,
            hot_values_by_col=hot_by_col,
        )

    def _declared_choices(self, col: ColDef) -> List[str]:
        return re.findall(r"'((?:''|[^'])*)'", col.declared_type or '')

    def _is_monetdb_family(self) -> bool:
        return self._dialect.optimizer_family() == 'monetdb'

    def _temporal_literal(self, dt: str, future: bool = False) -> str:
        if dt == 'DATE':
            year = random.randint(2030, 2039) if future else random.randint(1990, 2038)
            return f"'{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}'"
        if dt in ('DATETIME', 'TIMESTAMP'):
            year = random.randint(2030, 2039) if future else random.randint(1990, 2038)
            sec = random.randint(0, 59)
            micros = f".{random.randint(0, 999999):06d}" if random.random() < 0.5 else ''
            return (
                f"'{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d} "
                f"{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:{sec:02d}{micros}'"
            )
        if dt == 'TIME':
            return f"'{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}'"
        if dt == 'YEAR':
            return str(random.randint(2030, 2039) if future else random.randint(1990, 2038))
        return 'NULL'

    def _string_capacity(self, col: ColDef) -> int:
        return max(1, int(getattr(col, 'varchar_len', 32) or 32))

    def _truncate_sql_string_literal(self, literal: str, col: ColDef) -> str:
        if not (literal.startswith("'") and literal.endswith("'")):
            return literal
        payload = literal[1:-1]
        payload = payload[:self._string_capacity(col)]
        return f"'{payload}'"

    def _safe_string_samples(self, col: ColDef) -> List[str]:
        samples = ["'0'", "'1'", "'seed'", "'hv'"]
        return [self._truncate_sql_string_literal(sample, col) for sample in samples]

    def _fallback_non_null_literal(self, col: ColDef) -> str:
        dt = col.data_type
        if dt == 'INT':
            return '0'
        if dt in ('FLOAT', 'DOUBLE'):
            return '0.0'
        if dt == 'DECIMAL':
            return '0.00'
        if dt == 'DATE':
            return "'2000-01-01'"
        if dt in ('DATETIME', 'TIMESTAMP'):
            return "'2000-01-01 00:00:00'"
        if dt == 'TIME':
            return "'00:00:00'"
        if dt == 'YEAR':
            return '2000'
        if dt in _STRING_LIKE_TYPES:
            return self._truncate_sql_string_literal("'seed'", col)
        return '0'

    def _is_narrow_integer_declared_type(self, col: ColDef) -> bool:
        declared = (col.declared_type or '').upper()
        return (
            declared.startswith('SMALLINT')
            or declared.startswith('TINYINT')
            or declared.startswith('MEDIUMINT')
        )

    def _sanitize_insert_values(self, cols: List[ColDef], vals: List[str]) -> List[str]:
        sanitized: List[str] = []
        for col, val in zip(cols, vals):
            cur = val
            if col.is_primary_key and (cur is None or str(cur).upper() == 'NULL'):
                cur = str(random.randint(1, 10_000_000))
            if (not col.is_nullable) and (cur is None or str(cur).upper() == 'NULL'):
                cur = self._fallback_non_null_literal(col)
            if col.data_type in _STRING_LIKE_TYPES and isinstance(cur, str):
                cur = self._truncate_sql_string_literal(cur, col)
            sanitized.append(cur)
        return sanitized

    def _string_literal(self, col: ColDef, allow_trailing_spaces: bool = True) -> str:
        if col.data_type == 'ENUM':
            choices = self._declared_choices(col)
            if choices:
                return f"'{random.choice(choices)}'"
        if col.data_type == 'SET':
            choices = self._declared_choices(col)
            if choices:
                sample = random.sample(choices, k=random.randint(1, len(choices)))
                return "'" + ",".join(sample) + "'"
        if random.random() < 0.45:
            if self._is_monetdb_family():
                literal = random.choice(self._safe_string_samples(col))
            else:
                literal = random.choice([
                    "'0'", "'1'", "'-1'", "'0000-00-00'",
                    "'2023-01-01'", "'2023-01-01 00:00:00'",
                    "'not-a-date'", "'01e0'", "' 1'", "''",
                ])
        else:
            max_len = 64 if col.data_type == 'LONGTEXT' else min(20, self._string_capacity(col))
            n = random.randint(1, max(1, max_len))
            token = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789_-:/ ', k=n)).rstrip()
            literal = f"'{token or 'seed'}'"
        if allow_trailing_spaces and col.data_type == 'CHAR' and literal.startswith("'") and literal.endswith("'"):
            literal = literal[:-1] + '   ' + "'"
        return self._truncate_sql_string_literal(literal, col)

    def _create_hot_values(self, col: ColDef) -> List[str]:
        dt = col.data_type
        if dt in _TEMPORAL_TYPES:
            if self._is_monetdb_family():
                if dt == 'DATE':
                    return ["'2023-01-01'", "'1999-12-31'", "'2030-01-01'"]
                if dt in ('DATETIME', 'TIMESTAMP'):
                    return ["'2023-01-01 00:00:00'", "'2023-01-01 00:00:00.123456'", "'2030-01-01 12:00:00'"]
                if dt == 'TIME':
                    return ["'00:00:00'", "'12:34:56'", "'23:59:59'"]
                if dt == 'YEAR':
                    return ['1999', '2023', '2030']
            if dt == 'DATE':
                return ["'not-a-date'", "'2023-01-01'", "'0000-00-00'"]
            if dt in ('DATETIME', 'TIMESTAMP'):
                return ["'2023-01-01 00:00:00'", "'2023-01-01 00:00:00.123456'", "'not-a-date'"]
            if dt == 'TIME':
                return ["'00:00:00'", "'23:59:59'", "'not-a-time'"]
            if dt == 'YEAR':
                return ['1999', '2023', 'NULL']
        if dt == 'INT':
            a = random.randint(-16, 16)
            return [str(a), str(a+1+random.randint(0,3)), str(a-1-random.randint(0,3))]
        if dt in _STRING_LIKE_TYPES:
            if dt == 'ENUM':
                choices = self._declared_choices(col)
                if choices:
                    base = choices[:3] if len(choices) >= 3 else (choices + choices[:1] + choices[:1])[:3]
                    return [f"'{base[0]}'", f"'{base[1]}'", f"'{base[2]}'"]
            if dt == 'SET':
                choices = self._declared_choices(col)
                if choices:
                    first = choices[:2] if len(choices) >= 2 else choices
                    second = choices[-2:] if len(choices) >= 2 else choices
                    return [
                        "'" + ",".join(first[:1]) + "'",
                        "'" + ",".join(first) + "'",
                        "'" + ",".join(second) + "'",
                    ]
            s = f"hv_{random.randint(100,9999)}"
            if dt == 'LONGTEXT':
                if self._is_monetdb_family():
                    return [self._truncate_sql_string_literal(f"'{s}'", col), "'seed_long'", "'2023-01-01 00:00:00'"]
                return [f"'{s}'", "'not-a-date'", "'2023-01-01 00:00:00'"]
            if dt == 'CHAR':
                return [
                    self._truncate_sql_string_literal(f"'{s}   '", col),
                    self._truncate_sql_string_literal("'pad_me   '", col),
                    self._truncate_sql_string_literal("'2023-01-01   '", col),
                ]
            if self._is_monetdb_family():
                return [
                    self._truncate_sql_string_literal(f"'{s}'", col),
                    self._truncate_sql_string_literal("'seed'", col),
                    self._truncate_sql_string_literal("'2023-01-01'", col),
                ]
            return [f"'{s}'", "'not-a-date'", "'2023-01-01'"]
        if dt in ('FLOAT', 'DOUBLE'):
            a = random.randint(-200, 200) / 10.0
            return [f"{a:.3f}", f"{a+1.0:.3f}", f"{a-1.0:.3f}"]
        if dt == 'DECIMAL':
            a = random.randint(-1000, 1000) / 100.0
            return [f"{a:.2f}", f"{a+1.0:.2f}", f"{a-1.0:.2f}"]
        return ['NULL']

    def _create_expansion_hot_value(self, col: ColDef, existing: List[str]) -> str:
        dt = col.data_type
        if dt == 'INT':
            nums = [int(v) for v in existing if v != 'NULL']
            base = max(nums) if nums else 0
            for i in range(8):
                c = str(base + 20 + i)
                if c not in existing: return c
            return str(base + 40)
        if dt in _TEMPORAL_TYPES:
            return self._temporal_literal(dt, future=True)
        if dt in _STRING_LIKE_TYPES:
            for _ in range(16):
                c = self._string_literal(col)
                if c not in existing:
                    return c
            return f"'exp_final_{len(existing)}'"
        if dt in ('FLOAT', 'DOUBLE'):
            nums = [float(v) for v in existing if v != 'NULL']
            base = max(nums) if nums else 0.0
            for i in range(8):
                c = f"{base+20.0+i:.3f}"
                if c not in existing: return c
            return f"{base+40.0:.3f}"
        if dt == 'DECIMAL':
            nums = [float(v) for v in existing if v != 'NULL']
            base = max(nums) if nums else 0.0
            for i in range(8):
                c = f"{base+20.0+i:.2f}"
                if c not in existing: return c
            return f"{base+40.0:.2f}"
        return 'NULL'

    # ──────────────────────────────────────────
    # Step 2 & 4：数据插入（主表）
    # ──────────────────────────────────────────
    def _insert_hot_seed_rows(self, conn, table_name: str,
                               cols: List[ColDef], skew: SkewProfile, n: int):
        for _ in range(n):
            vals = [
                skew.primary_hot if c.name == skew.predicate_col.name
                else self._generate_value(c, skew, 0.5, 'baseline')
                for c in cols
            ]
            self._try_insert(conn, table_name, cols, vals)

    def _insert_skewed_rows(self, conn, table_name: str, cols: List[ColDef],
                             skew: SkewProfile, n: int, hotspot_prob: float, stage: str):
        for _ in range(n):
            vals = [self._generate_value(c, skew, hotspot_prob, stage) for c in cols]
            self._try_insert(conn, table_name, cols, vals)

    def _insert_noise_rows(self, conn, table_name: str, cols: List[ColDef], n: int):
        boundary_map = {}
        for c in cols:
            dt = c.data_type
            if dt == 'INT':
                if self._is_monetdb_family() and self._is_narrow_integer_declared_type(c):
                    boundary_map[c.name] = ['0', '1', '-1', '32767', '-32768', 'NULL']
                else:
                    boundary_map[c.name] = (
                        ['0', '1', '-1', '2147483647', '-2147483647', 'NULL']
                        if self._is_monetdb_family()
                        else ['0','1','-1','2147483647','-2147483648','NULL']
                    )
            elif dt in ('DATE', 'DATETIME', 'TIMESTAMP'):
                if self._is_monetdb_family():
                    boundary_map[c.name] = [
                        "'1000-01-01'", "'1999-12-31'", "'9999-12-31'",
                        "'2023-01-01'", "'2023-01-01 00:00:00.999999'", 'NULL'
                    ]
                else:
                    boundary_map[c.name] = [
                        "'0000-00-00'", "'1000-01-01'", "'9999-12-31'",
                        "'not-a-date'", "''", "'2023-02-29'", "'2023-01-01 00:00:00.999999'", 'NULL'
                    ]
            elif dt == 'TIME':
                boundary_map[c.name] = ["'00:00:00'", "'23:59:59'", 'NULL'] if self._is_monetdb_family() else ["'00:00:00'", "'23:59:59'", "'25:61:61'", "''", 'NULL']
            elif dt == 'YEAR':
                boundary_map[c.name] = ['1901', '1970', '2038', '2155', 'NULL']
            elif dt in _STRING_LIKE_TYPES:
                if self._is_monetdb_family():
                    boundary_map[c.name] = self._safe_string_samples(c) + ["'2023-01-01'", "'%'", "'_'", 'NULL']
                else:
                    boundary_map[c.name] = [
                        "''", "'NULL'", "'0'", "'1'", "'0000-00-00'",
                        "'2023-01-01'", "'not-a-date'", "'%'", "'_'", 'NULL'
                    ]
            elif dt in ('FLOAT','DOUBLE'):
                boundary_map[c.name] = ['0','0.0','-0.0','1.0','-1.0',
                                         '3.4028235E38','-3.4028235E38','NULL']
            elif dt == 'DECIMAL':
                boundary_map[c.name] = ['0','0.00','1.00','-1.00',
                                         '99999999.99','-99999999.99','NULL']
            else:
                boundary_map[c.name] = ['NULL']

        for _ in range(n):
            non_pk = [c for c in cols if not c.is_primary_key]
            target = random.choice(non_pk if non_pk else cols)
            bval   = random.choice(boundary_map[target.name])
            vals = [
                str(random.randint(1, 10_000_000)) if c.is_primary_key
                else (bval if c.name == target.name else 'NULL')
                for c in cols
            ]
            self._try_insert(conn, table_name, cols, vals)

    def _generate_value(self, col: ColDef, skew: SkewProfile,
                         hotspot_prob: float, stage: str) -> str:
        if col.is_primary_key:
            return str(random.randint(1, 10_000_000))
        use_hot  = random.random() < hotspot_prob
        hot_vals = skew.hot_values_by_col.get(col.name, []) if skew is not None else []
        exp_hot  = (
            skew.expansion_hot
            if skew is not None and col.name == skew.predicate_col.name
            else None
        )
        dt       = col.data_type
        if use_hot and hot_vals:
            if stage == 'expansion' and exp_hot and random.random() < 0.4:
                return exp_hot
            return random.choice(hot_vals)
        if dt == 'INT':    return str(random.randint(-1000, 1000))
        if dt in _TEMPORAL_TYPES:
            if (not self._is_monetdb_family()) and random.random() < 0.35:
                if dt == 'DATE':
                    return random.choice(["'not-a-date'", "'0000-00-00'", "''"])
                if dt in ('DATETIME', 'TIMESTAMP'):
                    return random.choice(["'not-a-date'", "'0000-00-00 00:00:00'", "''"])
                if dt == 'TIME':
                    return random.choice(["'not-a-time'", "'25:61:61'", "''"])
                if dt == 'YEAR':
                    return random.choice(['0000', '1901', '2155'])
            return self._temporal_literal(dt)
        if dt in _STRING_LIKE_TYPES:
            return self._string_literal(col)
        if dt in ('FLOAT','DOUBLE'): return f"{random.uniform(-1000, 1000):.3f}"
        if dt == 'DECIMAL':          return f"{random.uniform(-1000, 1000):.2f}"
        return 'NULL'

    def _insert_random_rows(self, conn, table_name: str, cols: List[ColDef], n: int):
        for _ in range(n):
            vals = [self._generate_value(c, None, 0.0, 'random') for c in cols]
            self._try_insert(conn, table_name, cols, vals)

    def _insert_aux_coercion_rows(self, conn, table_name: str, cols: List[ColDef], rows: int = 8):
        for i in range(rows):
            vals: List[str] = []
            for c in cols:
                if c.is_primary_key:
                    vals.append(str(9_000_000 + i))
                    continue
                dt = c.data_type
                if dt == 'INT':
                    if self._is_monetdb_family() and self._is_narrow_integer_declared_type(c):
                        vals.append(random.choice(['0', '1', '-1', '1024']))
                    else:
                        vals.append(random.choice(['0', '1', '-1', '20230101']))
                elif dt in _TEMPORAL_TYPES:
                    vals.append(self._temporal_literal(dt) if random.random() < 0.6 else 'NULL')
                elif dt in _STRING_LIKE_TYPES:
                    vals.append(self._string_literal(c))
                elif dt in ('FLOAT', 'DOUBLE'):
                    vals.append(random.choice(['0.0', '1.0', '-1.0']))
                elif dt == 'DECIMAL':
                    vals.append(random.choice(['0.00', '1.00', '-1.00']))
                else:
                    vals.append('NULL')
            self._try_insert(conn, table_name, cols, vals)

    def _try_insert(self, conn, table_name: str, cols: List[ColDef], vals: List[str]):
        vals = self._sanitize_insert_values(cols, vals)
        col_names = ', '.join(self._qi(c.name) for c in cols)
        insert_kw = self._dialect.get_insert_keyword(ignore_duplicates=True)
        sql = f"{insert_kw} INTO {table_name} ({col_names}) VALUES ({', '.join(vals)})"
        err = self._execute_best_effort_dml(conn, sql)
        if err is not None:
            self._log(f"  INSERT skipped: {err}")

    # ──────────────────────────────────────────
    # Step 4：ANALYZE TABLE
    # ──────────────────────────────────────────
    def _analyze_table(self, conn, table_name: str):
        if not self._ab.opt2_index_and_analyze:
            self._log("  [ablation] opt2 off: skipping ANALYZE")
            return
        sql = self._dialect.get_analyze_table_sql(table_name, self._current_schema_name())
        self._sql_log.append(sql + ';')
        self._log(f"  {sql}")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception as e:
            self._log(f"  ANALYZE failed: {e}")

    # ──────────────────────────────────────────
    # Step 6：验证单调性
    # ──────────────────────────────────────────
    def _verify(self, conn, spec: QuerySpec, s1: QuerySnapshot,
                 s2: QuerySnapshot, numeric_cols: List[ColDef]):
        """
        统一验证路径（所有查询均为行保留查询）：
          1. COUNT 单调：COUNT(S1) ≤ COUNT(S2)
          2. MAX  单调：MAX(col, S1) ≤ MAX(col, S2)，对所有 numeric 列
          3. MIN  单调：MIN(col, S1) ≥ MIN(col, S2)，对所有 numeric 列
          4. 行集合子集：row_digests(S1) ⊆ row_digests(S2)
        """
        try:
            self._verify_snapshot_consistency(s1, 'S1')
            self._verify_snapshot_consistency(s2, 'S2')
            self._verify_count(spec, s1, s2)
            for c in numeric_cols:
                self._verify_max(spec, c.name, s1, s2)
                self._verify_min(spec, c.name, s1, s2)
            if s1.row_digests:
                self._verify_row_subset(conn, spec, s1, s2)
        except AssertionError as e:
            if self._ab.disable_known_bug_suppression:
                raise
            known_bug_reason = self._known_mysql_subset_bug_reason(
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

    def _verify_dml_effect(
        self,
        spec: DMLEffectSpec,
        s1: DMLEffectSnapshot,
        s2: DMLEffectSnapshot,
    ) -> None:
        self._verify_dml_effect_keys(spec, s1, s2)
        self._verify_dml_effect_rowcount(spec, s1, s2)

    def _verify_dml_effect_keys(
        self,
        spec: DMLEffectSpec,
        s1: DMLEffectSnapshot,
        s2: DMLEffectSnapshot,
    ) -> None:
        missing: Dict[str, int] = {}
        remaining = dict(s2.affected_keys)
        for digest, cnt in s1.affected_keys.items():
            avail = remaining.get(digest, 0)
            if avail < cnt:
                missing[digest] = cnt - avail
            else:
                remaining[digest] = avail - cnt
        if missing:
            raise AssertionError(
                f"DML-effect key-set violation [{spec.dml_type}]: "
                f"{len(missing)} affected key digest(s) missing\n"
                f"  DML: {spec.dml_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}"
            )
        self._log(
            f"  DML-KEYS {sum(s1.affected_keys.values())} <= "
            f"{sum(s2.affected_keys.values())}  [PASS]"
        )

    def _verify_dml_effect_rowcount(
        self,
        spec: DMLEffectSpec,
        s1: DMLEffectSnapshot,
        s2: DMLEffectSnapshot,
    ) -> None:
        r1, r2 = s1.rows_affected, s2.rows_affected
        if r1 is None or r2 is None:
            return
        if spec.assertion == '<=' and r1 > r2:
            raise AssertionError(
                f"DML-effect rowcount violation [{spec.dml_type}]: "
                f"S1.rows_affected={r1} > S2.rows_affected={r2}\n"
                f"  DML: {spec.dml_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}"
            )
        if spec.assertion == '>=' and r1 < r2:
            raise AssertionError(
                f"DML-effect rowcount violation [{spec.dml_type}]: "
                f"S1.rows_affected={r1} < S2.rows_affected={r2}\n"
                f"  DML: {spec.dml_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}"
            )
        self._log(f"  DML-ROWS S1={r1} {spec.assertion} S2={r2}  [PASS]")

    def _known_mysql_subset_bug_reason(
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
        return None

    def _verify_count(self, spec, s1, s2):
        if s1.count is None or s2.count is None: return
        if s1.count > s2.count:
            raise AssertionError(
                f"COUNT violation: S1={s1.count} > S2={s2.count}\n"
                f"  Query: {spec.select_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}")
        self._log(f"  COUNT  S1={s1.count} <= S2={s2.count}  [PASS]")

    def _verify_max(self, spec, col, s1, s2):
        v1, v2 = s1.max_values.get(col), s2.max_values.get(col)
        if v1 is None or v2 is None:
            return
        if v1 > v2 + FLOAT_TOLERANCE:
            raise AssertionError(
                f"MAX({col}) violation: S1={v1} > S2={v2}\n"
                f"  Query: {spec.select_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}")
        self._log(f"  MAX({col}) S1={v1} <= S2={v2}  [PASS]")

    def _verify_min(self, spec, col, s1, s2):
        v1, v2 = s1.min_values.get(col), s2.min_values.get(col)
        if v1 is None or v2 is None:
            return
        if v2 > v1 + FLOAT_TOLERANCE:
            raise AssertionError(
                f"MIN({col}) violation: S2_min={v2} > S1_min={v1} "
                f"(expected S2_min <= S1_min)\n"
                f"  Query: {spec.select_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}")
        self._log(f"  MIN({col}) S1={v1} >= S2={v2}  [PASS]")

    def _verify_row_subset(self, conn, spec, s1, s2):
        if not s1.row_digests: return
        if s2.count is not None and s2.count <= 10000:
            s2_digests = s2.row_digests
        else:
            s2_digests = self._capture_row_digests(conn, spec.select_sql)
        missing    = {}
        remaining  = dict(s2_digests)
        for digest, cnt in s1.row_digests.items():
            avail = remaining.get(digest, 0)
            if avail < cnt:
                missing[digest] = cnt - avail
            else:
                remaining[digest] = avail - cnt
        if missing:
            raise AssertionError(
                f"ROW-SET subset violation: {len(missing)} digest(s) missing\n"
                f"  Query: {spec.select_sql}\n"
                f"  Plan1: {self._fmt_plan(s1.explain_plan)}\n"
                f"  Plan2: {self._fmt_plan(s2.explain_plan)}")
        s1t = sum(s1.row_digests.values())
        s2t = sum(s2_digests.values())
        self._log(f"  ROW-SET |S1|={s1t} ⊆ |S2|={s2t}  [PASS]")

    # ──────────────────────────────────────────
    # EXPLAIN 计划
    # ──────────────────────────────────────────
    def _verify_snapshot_consistency(
        self,
        snap: QuerySnapshot,
        label: str,
    ) -> None:
        if not snap.snapshot_consistency_error:
            return
        raise AssertionError(
            f"{label} snapshot inconsistency:\n"
            f"  {snap.snapshot_consistency_error}\n"
            f"  Plan: {self._fmt_plan(snap.explain_plan)}"
        )

    def _capture_explain(self, conn, select_sql: str) -> List[str]:
        rows = []
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {select_sql}")   # 去掉 FORMAT=TRADITIONAL，兼容性更好
                if not cur.description:
                    return rows
                col_names = [d[0].lower() for d in cur.description]

                def gcol(row, name):
                    try:
                        v = row[col_names.index(name)]
                        return str(v) if v is not None else 'null'
                    except (ValueError, IndexError):
                        return 'null'

                for row in cur.fetchall():
                    rows.append(
                        f"id={gcol(row,'id')};"
                        f"select_type={gcol(row,'select_type')};"
                        f"table={gcol(row,'table')};"
                        f"type={gcol(row,'type')};"
                        f"possible_keys={gcol(row,'possible_keys')};"
                        f"key={gcol(row,'key')};"
                        f"key_len={gcol(row,'key_len')};"
                        f"rows={gcol(row,'rows')};"
                        f"filtered={gcol(row,'filtered')};"
                        f"extra={gcol(row,'extra')}"
                    )
        except Exception as e:
            self._log(f"  EXPLAIN failed: {e}")
        return rows

    def _capture_explain_traditional(self, conn, select_sql: str) -> List[str]:
        rows = []
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN FORMAT=TRADITIONAL {select_sql}")
                if not cur.description:
                    return rows
                col_names = [d[0].lower() for d in cur.description]

                def gcol(row, name):
                    try:
                        v = row[col_names.index(name)]
                        return str(v) if v is not None else 'null'
                    except (ValueError, IndexError):
                        return 'null'

                for row in cur.fetchall():
                    rows.append(
                        f"id={gcol(row,'id')};"
                        f"select_type={gcol(row,'select_type')};"
                        f"table={gcol(row,'table')};"
                        f"type={gcol(row,'type')};"
                        f"possible_keys={gcol(row,'possible_keys')};"
                        f"key={gcol(row,'key')};"
                        f"key_len={gcol(row,'key_len')};"
                        f"rows={gcol(row,'rows')};"
                        f"filtered={gcol(row,'filtered')};"
                        f"extra={gcol(row,'extra')}"
                    )
        except Exception as e:
            self._log(f"  EXPLAIN FORMAT=TRADITIONAL failed: {e}")
        return rows

    def _plans_equivalent(self, p1: List[str], p2: List[str]) -> bool:
        if len(p1) != len(p2): return False
        return all(
            self._normalize_plan_row(r1) == self._normalize_plan_row(r2)
            for r1, r2 in zip(p1, p2)
        )

    def _normalize_plan_row(self, row: str) -> str:
        row = re.sub(r'rows=[^;]+',     'rows=?',     row)
        row = re.sub(r'filtered=[^;]+', 'filtered=?', row)
        row = re.sub(r'key_len=[^;]+',  'key_len=?',  row)
        dialect = getattr(self, '_dialect', get_current_dialect())
        if dialect.optimizer_family() == 'monetdb':
            row = re.sub(r'\s+', ' ', row)
            row = re.sub(r'estimated\s+[0-9]+(?:\.[0-9]+)?', 'estimated ?', row, flags=re.IGNORECASE)
        if dialect.optimizer_family() == 'polardb':
            row = re.sub(
                r'([A-Za-z0-9_]+_[0-9a-f]{8}(?:_t[23])?)_[A-Za-z0-9]{4}\b',
                r'\1_?',
                row,
                flags=re.IGNORECASE,
            )
            row = re.sub(r'TemplateId:\s*[0-9a-f]+', 'TemplateId: ?', row, flags=re.IGNORECASE)
        row = re.sub(r'_s[12]_([0-9a-f]{8})', r'_s?_\1', row, flags=re.IGNORECASE)
        row = re.sub(r'\|\s*\d+\s*\|\s*\d+\s*\|$', '|?|?|', row)
        return row.strip()

    def _uses_mysql_like_known_bug_workaround_family(self) -> bool:
        if self._ab.disable_known_bug_suppression:
            return False
        if not getattr(self, '_enable_known_mysql_date_index_string_eq_workaround', False):
            return False
        dialect = getattr(self, '_dialect', get_current_dialect())
        return dialect.optimizer_family() in ('mysql', 'percona')

    def _is_known_mysql_null_contradiction_query(self, select_sql: str) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.upper()).strip()
        if ' OR ' in normalized:
            return False

        is_null_refs = set(re.findall(r"(`[^`]+`\.`[^`]+`)\s+IS\s+NULL\b", normalized))
        is_not_null_refs = set(re.findall(r"(`[^`]+`\.`[^`]+`)\s+IS\s+NOT\s+NULL\b", normalized))
        return bool(is_null_refs & is_not_null_refs)

    def _is_known_mysql_year_string_in_subquery_bug(self, conn, select_sql: str) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if '/*IMPLICIT_CONVERSION_IN*/' not in upper:
            return False
        if ' IN (' not in upper or 'SELECT ' not in upper:
            return False

        parsed = self._extract_in_subquery_type_probe_refs(normalized)
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

    def _is_known_mysql_exists_year_materialization_bug(
        self,
        conn,
        select_sql: str,
        s1_plan: List[str],
        s2_plan: List[str],
    ) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if ' EXISTS (' not in upper or 'SELECT 1' not in upper:
            return False
        if ' ORDER BY ' in upper:
            return False
        if 'CAST(' in upper:
            return False
        if not self._plan_has_firstmatch(s1_plan):
            return False
        if not self._plan_has_materialized_exists_lookup(s2_plan):
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

    def _is_known_mysql_implicit_date_string_join_bug(
        self,
        conn,
        select_sql: str,
    ) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if '/*IMPLICIT_CONVERSION_JOIN*/' not in upper:
            return False
        if ' JOIN ' not in upper or ' EXISTS (' in upper or ' IN (' in upper:
            return False

        alias_map = self._extract_table_alias_map(normalized)
        if not alias_map:
            return False

        for left_alias, left_col, right_alias, right_col in self._extract_ref_equality_pairs(normalized):
            if left_alias == right_alias:
                continue
            left_table = alias_map.get(left_alias)
            right_table = alias_map.get(right_alias)
            if left_table is None or right_table is None:
                continue
            left_type = self._lookup_column_type(conn, left_table, left_col)
            right_type = self._lookup_column_type(conn, right_table, right_col)
            if self._is_date_string_sql_type_pair(left_type, right_type):
                return True
        return False

    def _is_known_mysql_cast_string_to_date_join_bug(
        self,
        conn,
        select_sql: str,
    ) -> bool:
        if not self._uses_mysql_like_known_bug_workaround_family():
            return False

        normalized = re.sub(r'\s+', ' ', select_sql.strip())
        upper = normalized.upper()
        if ' JOIN ' not in upper or 'CAST(' not in upper or ' AS DATE)' not in upper:
            return False

        alias_map = self._extract_table_alias_map(normalized)
        if not alias_map:
            return False

        cast_patterns = [
            (
                r"CAST\(`([^`]+)`\.`([^`]+)` AS DATE\)\s*=\s*`([^`]+)`\.`([^`]+)`",
                True,
            ),
            (
                r"`([^`]+)`\.`([^`]+)`\s*=\s*CAST\(`([^`]+)`\.`([^`]+)` AS DATE\)",
                False,
            ),
        ]
        for pattern, cast_on_left in cast_patterns:
            for match in re.finditer(pattern, normalized, re.IGNORECASE):
                left_alias, left_col, right_alias, right_col = match.groups()
                left_table = alias_map.get(left_alias)
                right_table = alias_map.get(right_alias)
                if left_table is None or right_table is None:
                    continue
                left_type = self._lookup_column_type(conn, left_table, left_col)
                right_type = self._lookup_column_type(conn, right_table, right_col)
                cast_source_type = left_type if cast_on_left else right_type
                date_side_type = right_type if cast_on_left else left_type
                if (
                    cast_source_type is not None
                    and date_side_type is not None
                    and self._is_string_like_sql_type(cast_source_type)
                    and date_side_type == 'DATE'
                ):
                    return True
        return False

    def _extract_in_subquery_type_probe_refs(self, select_sql: str):
        outer_match = re.search(
            r"(`[^`]+`\.`[^`]+`)\s+IN\s*\(\s*SELECT\s+(`[^`]+`\.`[^`]+`)\s+FROM\s+`([^`]+)`",
            select_sql,
            re.IGNORECASE,
        )
        if not outer_match:
            return None

        outer_ref = outer_match.group(1)
        inner_ref = outer_match.group(2)
        inner_table = outer_match.group(3)
        outer_parts = re.findall(r'`([^`]+)`', outer_ref)
        inner_parts = re.findall(r'`([^`]+)`', inner_ref)
        if len(outer_parts) != 2 or len(inner_parts) != 2:
            return None

        outer_col = outer_parts[1]
        inner_col = inner_parts[1]

        table_matches = re.findall(r'FROM\s+`([^`]+)`', select_sql, re.IGNORECASE)
        if not table_matches:
            return None
        outer_table = table_matches[0]
        return (outer_table, outer_col, inner_table, inner_col)

    def _extract_exists_type_probe_refs(self, select_sql: str):
        outer_match = re.search(
            r"FROM\s+`([^`]+)`\s+([A-Za-z_][A-Za-z0-9_]*)",
            select_sql,
            re.IGNORECASE,
        )
        inner_match = re.search(
            r"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+`([^`]+)`\s+([A-Za-z_][A-Za-z0-9_]*)",
            select_sql,
            re.IGNORECASE,
        )
        if not outer_match or not inner_match:
            return None

        outer_table, outer_alias = outer_match.group(1), outer_match.group(2)
        inner_table, inner_alias = inner_match.group(1), inner_match.group(2)

        pair_patterns = [
            rf"`{re.escape(outer_alias)}`\.`([^`]+)`\s*=\s*`{re.escape(inner_alias)}`\.`([^`]+)`",
            rf"`{re.escape(inner_alias)}`\.`([^`]+)`\s*=\s*`{re.escape(outer_alias)}`\.`([^`]+)`",
        ]
        for i, pattern in enumerate(pair_patterns):
            match = re.search(pattern, select_sql, re.IGNORECASE)
            if not match:
                continue
            if i == 0:
                outer_col, inner_col = match.group(1), match.group(2)
            else:
                inner_col, outer_col = match.group(1), match.group(2)
            return (outer_table, outer_col, inner_table, inner_col)
        return None

    def _extract_table_alias_map(self, select_sql: str) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for table_name, alias in re.findall(
            r"(?:FROM|JOIN)\s+`([^`]+)`\s+([A-Za-z_][A-Za-z0-9_]*)",
            select_sql,
            re.IGNORECASE,
        ):
            alias_map[alias] = table_name
        return alias_map

    def _extract_ref_equality_pairs(self, select_sql: str) -> List[Tuple[str, str, str, str]]:
        pairs: List[Tuple[str, str, str, str]] = []
        for match in re.finditer(
            r"`([^`]+)`\.`([^`]+)`\s*=\s*`([^`]+)`\.`([^`]+)`",
            select_sql,
            re.IGNORECASE,
        ):
            pairs.append(match.groups())
        return pairs

    def _plan_has_firstmatch(self, plan: List[str]) -> bool:
        return any('FirstMatch(' in row for row in plan)

    def _plan_has_materialized_exists_lookup(self, plan: List[str]) -> bool:
        has_materialized = any('select_type=MATERIALIZED' in row for row in plan)
        has_subquery_lookup = any('table=<subquery2>' in row for row in plan)
        return has_materialized and has_subquery_lookup

    def _lookup_column_type(self, conn, table_name: str, col_name: str) -> Optional[str]:
        try:
            with conn.cursor() as cur:
                if self._dialect.optimizer_family() == 'monetdb':
                    schema_name = self._current_schema_name()
                    if not schema_name:
                        current_schema_sql = self._dialect.get_current_schema_sql()
                        if not current_schema_sql:
                            return None
                        cur.execute(current_schema_sql)
                        row = cur.fetchone()
                        schema_name = row[0] if row and row[0] else None
                        self._current_schema = schema_name
                    if not schema_name:
                        return None
                    cur.execute(
                        """
                        SELECT UPPER(type)
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                          AND column_name = %s
                        """,
                        (schema_name, table_name, col_name),
                    )
                else:
                    cur.execute("SELECT DATABASE()")
                    row = cur.fetchone()
                    current_db = row[0] if row else None
                    if not current_db:
                        return None
                    cur.execute(
                        """
                        SELECT UPPER(DATA_TYPE)
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                          AND column_name = %s
                        """,
                        (current_db, table_name, col_name),
                    )
                row = cur.fetchone()
                return row[0] if row and row[0] else None
        except Exception:
            return None

    def _is_string_like_sql_type(self, data_type: str) -> bool:
        return data_type in _STRING_LIKE_TYPES

    def _is_date_string_sql_type_pair(
        self,
        left_type: Optional[str],
        right_type: Optional[str],
    ) -> bool:
        if left_type is None or right_type is None:
            return False
        return (
            (left_type == 'DATE' and self._is_string_like_sql_type(right_type))
            or (right_type == 'DATE' and self._is_string_like_sql_type(left_type))
        )

    # ──────────────────────────────────────────
    # 行摘要
    # ──────────────────────────────────────────
    def _capture_row_digests(self, conn, select_sql: str) -> Dict[str, int]:
        digests, _ = self._capture_row_digests_with_status(conn, select_sql)
        return digests or {}

    def _capture_row_digests_with_status(
        self,
        conn,
        select_sql: str,
    ) -> Tuple[Optional[Dict[str, int]], bool]:
        digests: Dict[str, int] = {}
        try:
            with conn.cursor() as cur:
                cur.execute(select_sql)
                for row in cur.fetchall():
                    d = self._row_digest(row)
                    digests[d] = digests.get(d, 0) + 1
        except ConnectionLevelQueryError:
            raise
        except Exception as e:
            self._raise_if_connection_level_error(e)
            if self._is_expected_query_runtime_error(e):
                self._rollback_after_statement_error(conn)
                raise IgnorableQueryRuntimeError(str(e)) from e
            self._rollback_after_statement_error(conn)
            self._log(f"  row digest capture failed: {e}")
            return None, False
        return digests, True

    def _set_snapshot_consistency_error_if_needed(
        self,
        snap: QuerySnapshot,
        sql: str,
    ) -> None:
        if snap.count is None:
            return
        enumerated_rows = sum(snap.row_digests.values())
        if snap.count == enumerated_rows:
            return
        snap.snapshot_consistency_error = (
            "Intra-snapshot COUNT/digest inconsistency: "
            f"COUNT(*)={snap.count} but row enumeration returned {enumerated_rows} rows.\n"
            f"  Query: {sql}"
        )

    def _should_skip_snapshot_on_consistency_error(
        self,
        snap: QuerySnapshot,
    ) -> bool:
        if not snap.snapshot_consistency_error:
            return False
        return self._dialect.optimizer_family() == 'polardb'

    def _row_digest(self, row: tuple) -> str:
        h = hashlib.sha256()
        for i, val in enumerate(row):
            if i > 0:
                h.update(b'|')
            s = self._canonicalize_digest_value(val)
            h.update(s.encode('utf-8'))
        return h.hexdigest()

    def _uses_tolerant_float_digest_family(self) -> bool:
        dialect = getattr(self, '_dialect', None)
        return bool(dialect and dialect.optimizer_family() == 'tidb')

    def _canonicalize_float_digest_value(self, val: float) -> str:
        if val == 0.0:
            return '0'

        text = str(val)
        if self._uses_tolerant_float_digest_family():
            # TiDB can render mathematically identical DOUBLE results with
            # 1-ULP textual drift across S1/S2, which should not break subset
            # comparisons when the row is otherwise unchanged.
            text = format(val, '.15g')

        try:
            normalized = Decimal(text).normalize()
        except InvalidOperation:
            return text

        if normalized == 0:
            return '0'
        return format(normalized, 'f')

    def _canonicalize_digest_value(self, val) -> str:
        if val is None:
            return 'NULL'
        if isinstance(val, bool):
            return '1' if val else '0'
        if isinstance(val, int):
            return str(val)
        if isinstance(val, Decimal):
            if not val.is_finite():
                return str(val)
            normalized = val.normalize()
            if normalized == 0:
                return '0'
            return format(normalized, 'f')
        if isinstance(val, float):
            return self._canonicalize_float_digest_value(val)
        return str(val).rstrip()

    # ──────────────────────────────────────────
    # Bug 日志
    # ──────────────────────────────────────────
    def _log_bug(self, error_msg: str, spec: QuerySpec,
                  s1: QuerySnapshot, s2: QuerySnapshot, uid: str):
        log_path = os.path.join(
            self._log_dir,
            f'SubsetOracle_bugs_{time.strftime("%Y%m%d_%H%M%S")}.log'
        )
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{ts}] Round #{uid} BUG DETECTED\n")
            f.write(f"SELECT SQL   : {spec.select_sql}\n")
            f.write(f"Plan S1      : {self._fmt_plan(s1.explain_plan)}\n")
            f.write(f"Plan S2      : {self._fmt_plan(s2.explain_plan)}\n")
            f.write(f"S1 count     : {s1.count}\n")
            f.write(f"S2 count     : {s2.count}\n")
            f.write(f"Error        : {error_msg}\n")
            f.write(f"\n-- 完整复现序列 ({len(self._sql_log)} statements) --\n")
            for sql in self._sql_log:
                f.write(sql + '\n')
            f.write(f"\n-- 验证查询 --\n")
            f.write(f"{spec.select_sql};\n")
        self._log(f"  [BUG] Logged to {log_path}")
        print(f"[SubsetOracle] BUG DETECTED: {error_msg[:120]}")

    def _log_dml_bug(
        self,
        error_msg: str,
        spec: DMLEffectSpec,
        s1: DMLEffectSnapshot,
        s2: DMLEffectSnapshot,
        uid: str,
    ):
        log_path = os.path.join(
            self._log_dir,
            f'SubsetOracle_bugs_{time.strftime("%Y%m%d_%H%M%S")}.log'
        )
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{ts}] Round #{uid} DML BUG DETECTED\n")
            f.write(f"DML SQL      : {spec.dml_sql}\n")
            f.write(f"DML type     : {spec.dml_type}\n")
            f.write(f"Key SELECT   : {spec.key_select_sql}\n")
            f.write(f"Plan S1      : {self._fmt_plan(s1.explain_plan)}\n")
            f.write(f"Plan S2      : {self._fmt_plan(s2.explain_plan)}\n")
            f.write(f"S1 rows      : {s1.rows_affected}\n")
            f.write(f"S2 rows      : {s2.rows_affected}\n")
            f.write(
                f"S1 key count : {sum(s1.affected_keys.values())}\n"
            )
            f.write(
                f"S2 key count : {sum(s2.affected_keys.values())}\n"
            )
            f.write(f"Error        : {error_msg}\n")
            f.write(f"\n-- 瀹屾暣澶嶇幇搴忓垪 ({len(self._sql_log)} statements) --\n")
            for sql in self._sql_log:
                f.write(sql + '\n')
            f.write(f"\n-- 楠岃瘉 DML --\n")
            f.write(f"{spec.key_select_sql};\n")
            f.write(f"{spec.dml_sql};\n")
        self._log(f"  [BUG] Logged to {log_path}")
        print(f"[SubsetOracle] DML BUG DETECTED: {error_msg[:120]}")

    # ──────────────────────────────────────────
    # 数据库工具方法
    # ──────────────────────────────────────────
    def _qi(self, name: str) -> str:
        return self._dialect.quote_identifier(name)

    def _current_schema_name(self) -> Optional[str]:
        return getattr(self, '_current_schema', None)

    def _begin_setup_transaction(self, conn) -> None:
        if not self._is_monetdb_family():
            return
        self._monetdb_tx_active = True

    def _target_baseline_queries(self) -> int:
        return TARGET_BASELINE_QUERIES

    def _minimum_baseline_queries(self) -> int:
        return MIN_BASELINE_QUERIES

    def _max_query_gen_attempts(self) -> int:
        return 360 if self._is_monetdb_family() else MAX_QUERY_GEN_ATTEMPTS

    def _min_main_table_queries(self, target_baseline_queries: int) -> int:
        if self._is_monetdb_family():
            return 1
        return target_baseline_queries // 2

    def _min_risky_baseline_queries(self, vs_tables) -> int:
        if self._is_monetdb_family():
            return 0
        return 1 if len(vs_tables) > 1 else 0

    def _rewrite_generated_insert(self, sql: str, source_table: str, actual_table: str) -> str:
        insert_kw = self._dialect.get_insert_keyword(ignore_duplicates=True)
        rewritten = sql.replace(
            f"INSERT INTO {source_table}",
            f"{insert_kw} INTO {actual_table}",
        )
        rewritten = rewritten.replace(
            f"INSERT  INTO {source_table}",
            f"{insert_kw} INTO {actual_table}",
        )
        return rewritten

    def _column_type_code_is_numeric(self, type_code) -> bool:
        if type_code in _MYSQL_NUMERIC_FIELD_TYPES:
            return True
        text = str(type_code).strip().lower()
        if not text:
            return False
        return any(
            text == numeric_name or numeric_name in text
            for numeric_name in _GENERIC_NUMERIC_TYPE_NAMES
        )

    def _connect(self):
        try:
            self._current_schema = None
            self._monetdb_tx_active = False
            if self._dialect.optimizer_family() == 'monetdb':
                import pymonetdb

                conn = pymonetdb.connect(
                    hostname=self.db_config.get('host', '127.0.0.1'),
                    port=self.db_config.get('port', 50000),
                    username=self.db_config.get('user', 'monetdb'),
                    password=self.db_config.get('password', 'monetdb'),
                    database=self.db_config.get('database', 'demo'),
                )
                try:
                    conn.autocommit = True
                except Exception:
                    pass
                self._monetdb_tx_active = True
            else:
                conn = pymysql.connect(
                    host=self.db_config.get('host', '127.0.0.1'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    charset='utf8mb4',
                    autocommit=True,
                )
            db_name = self.db_config.get('database', 'test')
            with conn.cursor() as cur:
                create_sql = self._dialect.get_create_database_sql(db_name)
                if create_sql:
                    cur.execute(create_sql)
                    self._sql_log.append(create_sql + ';')
                use_sql = self._dialect.get_use_database_sql(db_name)
                if use_sql:
                    cur.execute(use_sql)
                    self._sql_log.append(use_sql + ';')
                schema_name = self.db_config.get('schema')
                set_schema_sql = self._dialect.get_set_schema_sql(schema_name) if schema_name else None
                if set_schema_sql:
                    cur.execute(set_schema_sql)
                    self._sql_log.append(set_schema_sql + ';')
                current_schema_sql = self._dialect.get_current_schema_sql()
                if current_schema_sql:
                    cur.execute(current_schema_sql)
                    row = cur.fetchone()
                    self._current_schema = row[0] if row and row[0] else schema_name
                if self._dialect.optimizer_family() in ('tidb', 'oceanbase', 'polardb'):
                    # Relax strict session modes so boundary / invalid literals are
                    # coerced instead of being rejected before the oracle can exercise them.
                    session_sql = "SET SESSION sql_mode = ''"
                    cur.execute(session_sql)
                    self._sql_log.append(session_sql + ';')
                if self._dialect.optimizer_family() == 'oceanbase':
                    # Keep string comparisons and DISTINCT semantics case-sensitive
                    # for newly created OceanBase repro databases and sessions.
                    session_sql = "SET SESSION collation_connection = 'utf8mb4_bin'"
                    cur.execute(session_sql)
                    self._sql_log.append(session_sql + ';')
            return conn
        except Exception as e:
            self._log(f"  DB connect failed: {e}")
            return None

    def _rollback_after_statement_error(self, conn) -> None:
        if not self._is_monetdb_family():
            return
        try:
            conn.rollback()
            self._monetdb_tx_active = True
        except Exception:
            try:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK")
                self._monetdb_tx_active = True
            except Exception:
                pass

    def _commit_setup_barrier(self, conn) -> None:
        if not self._is_monetdb_family():
            return
        try:
            conn.commit()
            self._monetdb_tx_active = True
        except Exception:
            try:
                with conn.cursor() as cur:
                    cur.execute("COMMIT")
                self._monetdb_tx_active = True
            except Exception:
                pass

    def _execute_best_effort_dml(self, conn, sql: str) -> Optional[Exception]:
        if self._is_monetdb_family() and getattr(self, '_monetdb_tx_active', False):
            savepoint = f"sp_{random.randint(100000, 999999)}"
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SAVEPOINT {savepoint}")
                self._exec_dml(conn, sql)
                return None
            except Exception as e:
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                except Exception:
                    self._rollback_after_statement_error(conn)
                return e

        try:
            self._exec_dml(conn, sql)
            return None
        except Exception as e:
            self._rollback_after_statement_error(conn)
            return e

    def _begin_transaction(self, conn) -> None:
        if self._dialect.optimizer_family() == 'monetdb':
            self._monetdb_tx_active = True
            return
        conn.begin()

    def _commit_transaction(self, conn) -> None:
        conn.commit()
        if self._is_monetdb_family():
            self._monetdb_tx_active = True

    def _rollback_transaction(self, conn) -> None:
        try:
            conn.rollback()
            if self._is_monetdb_family():
                self._monetdb_tx_active = True
        except Exception:
            self._rollback_after_statement_error(conn)

    def _exec_ddl(self, conn, sql: str, ignore_error: bool = False):
        self._sql_log.append(sql + ';')
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception as e:
            self._raise_if_connection_level_error(e)
            if not ignore_error:
                raise
            self._log(f"  DDL ignored error: {e}")

    def _exec_dml(self, conn, sql: str):
        self._sql_log.append(sql + ';')
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception as e:
            self._raise_if_connection_level_error(e)
            raise

    def _exec_single_int(self, conn, sql: str) -> Optional[int]:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else None
        except Exception as e:
            self._raise_if_connection_level_error(e)
            if self._is_expected_query_runtime_error(e):
                self._rollback_after_statement_error(conn)
                raise IgnorableQueryRuntimeError(str(e)) from e
            self._rollback_after_statement_error(conn)
            self._log(f"  exec_single_int failed: {e}")
            return None

    def _exec_single_float(self, conn, sql: str) -> Optional[float]:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return float(row[0]) if row and row[0] is not None else None
        except Exception as e:
            self._raise_if_connection_level_error(e)
            if self._is_expected_query_runtime_error(e):
                self._rollback_after_statement_error(conn)
                raise IgnorableQueryRuntimeError(str(e)) from e
            self._rollback_after_statement_error(conn)
            self._log(f"  exec_single_float failed: {e}")
            return None

    def _drop_if_exists(self, conn, table_name: str):
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {table_name}")
        except Exception:
            pass

    def _is_expected_query_runtime_error(self, err: Exception) -> bool:
        code = None
        if getattr(err, 'args', None):
            first = err.args[0]
            if isinstance(first, int):
                code = first

        if code in self._ignorable_codes:
            return True

        msg = str(err).lower()
        return any(pat in msg for pat in self._ignorable_patterns)

    def _is_connection_level_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(pat in msg for pat in _CONNECTION_LEVEL_ERROR_PATTERNS)

    def _raise_if_connection_level_error(self, err: Exception):
        if self._is_connection_level_error(err):
            raise ConnectionLevelQueryError(str(err)) from err

    # ──────────────────────────────────────────
    # 日志
    # ──────────────────────────────────────────
    def _log(self, msg: str):
        if self.verbose:
            print(msg)
        if self.log_file:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')

    def _fmt_plan(self, plan: List[str]) -> str:
        return ' | '.join(plan) if plan else '[]'

    def _capture_explain(self, conn, select_sql: str) -> List[str]:
        rows: List[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {select_sql}")
                if self._dialect.optimizer_family() == 'monetdb':
                    for row in cur.fetchall():
                        rendered = " | ".join(
                            str(val) for val in row
                            if val is not None and str(val).strip()
                        )
                        if rendered:
                            rows.append(rendered)
                    return rows
                if not cur.description:
                    return rows
                col_names = [d[0].lower() for d in cur.description]
                for row in cur.fetchall():
                    parts = [
                        f"{name}={val if val is not None else 'null'}"
                        for name, val in zip(col_names, row)
                    ]
                    rows.append(';'.join(parts))
        except Exception as e:
            self._raise_if_connection_level_error(e)
            self._rollback_after_statement_error(conn)
            self._log(f"  EXPLAIN failed: {e}")
        return rows

    def _capture_explain_traditional(self, conn, select_sql: str) -> List[str]:
        if not self._dialect.supports_explain_format_traditional():
            return self._capture_explain(conn, select_sql)

        rows: List[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN FORMAT=TRADITIONAL {select_sql}")
                if not cur.description:
                    return rows
                col_names = [d[0].lower() for d in cur.description]

                def gcol(row, name):
                    try:
                        v = row[col_names.index(name)]
                        return str(v) if v is not None else 'null'
                    except (ValueError, IndexError):
                        return 'null'

                for row in cur.fetchall():
                    rows.append(
                        f"id={gcol(row,'id')};"
                        f"select_type={gcol(row,'select_type')};"
                        f"table={gcol(row,'table')};"
                        f"type={gcol(row,'type')};"
                        f"possible_keys={gcol(row,'possible_keys')};"
                        f"key={gcol(row,'key')};"
                        f"key_len={gcol(row,'key_len')};"
                        f"rows={gcol(row,'rows')};"
                        f"filtered={gcol(row,'filtered')};"
                        f"extra={gcol(row,'extra')}"
                    )
        except Exception as e:
            self._raise_if_connection_level_error(e)
            self._rollback_after_statement_error(conn)
            self._log(f"  EXPLAIN FORMAT=TRADITIONAL failed, falling back: {e}")
            return self._capture_explain(conn, select_sql)
        return rows

    def _plans_equivalent(self, p1: List[str], p2: List[str]) -> bool:
        if not p1 or not p2:
            return False
        if len(p1) != len(p2):
            return False
        return all(
            self._normalize_plan_row(r1) == self._normalize_plan_row(r2)
            for r1, r2 in zip(p1, p2)
        )

    def _normalize_plan_row(self, row: str) -> str:
        row = re.sub(r'rows=[^;]+', 'rows=?', row, flags=re.IGNORECASE)
        row = re.sub(r'filtered=[^;]+', 'filtered=?', row, flags=re.IGNORECASE)
        row = re.sub(r'key_len=[^;]+', 'key_len=?', row, flags=re.IGNORECASE)
        row = re.sub(r'estrows=[^;]+', 'estrows=?', row, flags=re.IGNORECASE)
        row = re.sub(r'HitCache:\s*(true|false)', 'HitCache:?', row, flags=re.IGNORECASE)
        dialect = getattr(self, '_dialect', get_current_dialect())
        if dialect.optimizer_family() == 'polardb':
            row = re.sub(
                r'([A-Za-z0-9_]+_[0-9a-f]{8}(?:_t[23])?)_[A-Za-z0-9]{4}\b',
                r'\1_?',
                row,
                flags=re.IGNORECASE,
            )
            row = re.sub(r'TemplateId:\s*[0-9a-f]+', 'TemplateId: ?', row, flags=re.IGNORECASE)
        row = re.sub(r'_s[12]_([0-9a-f]{8})', r'_s?_\1', row, flags=re.IGNORECASE)
        row = re.sub(r'\|\s*\d+\s*\|\s*\d+\s*\|$', '|?|?|', row)
        return row.strip()
