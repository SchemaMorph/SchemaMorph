"""
oracle/subset_query_gen.py

Subset-oriented SQL generator for SubsetOracle.

Design goal:
- preserve row-level monotonicity / subset behavior for S1 subset S2
- expand plan diversity and query surface area
- avoid constructs that obviously violate the oracle's semantics
"""

import random
import re
import traceback
from typing import Any, Dict, List, Optional, Tuple
from data_structures.db_dialect import get_current_dialect


_NUM_SCALAR_FUNS: List[str] = [
    'ABS', 'ROUND', 'CEIL', 'FLOOR', 'SIGN',
    'SQRT', 'SIN', 'COS', 'TAN', 'EXP', 'LOG',
]

_STR_SCALAR_FUNS: List[str] = [
    'UPPER', 'LOWER', 'TRIM', 'LTRIM', 'RTRIM', 'REVERSE',
]

_ARITH_OPS: List[str] = ['+', '-', '*']

_NO_JOIN_TYPES: frozenset = frozenset({'FLOAT', 'DOUBLE', 'DECIMAL', 'OPAQUE'})
_INT_FAMILY: frozenset = frozenset({'INT', 'BIGINT', 'SMALLINT', 'TINYINT'})
_NUMERIC_FAMILY: frozenset = frozenset({'INT', 'BIGINT', 'SMALLINT', 'TINYINT',
                                        'FLOAT', 'DOUBLE', 'DECIMAL'})
_STRING_FAMILY: frozenset = frozenset({'VARCHAR', 'TEXT', 'LONGTEXT', 'CHAR', 'ENUM', 'SET'})
_TEMPORAL_FAMILY: frozenset = frozenset({'DATE', 'DATETIME', 'TIMESTAMP', 'TIME', 'YEAR'})

_SELECT_MIN_COLS = 1
_SELECT_MAX_COLS = 5
_WHERE_MAX_PREDS = 3
_MAX_GENERATE_RETRIES = 16
_ORDER_BY_PROB = 0.30


class SubsetQueryGenerator:
    def __init__(
        self,
        tables: List[Tuple[str, List[Any]]],
        skew_hot_values: Optional[Dict[str, Dict[str, List[str]]]] = None,
        dialect=None,
        enable_known_mysql_date_index_string_eq_workaround: Optional[bool] = None,
        main_table_name: Optional[str] = None,
        ablation_config=None,
    ) -> None:
        if not tables:
            raise ValueError("SubsetQueryGenerator: at least one table is required")
        self.tables = tables
        self.main_table_name = main_table_name or tables[0][0]
        self.hot_values = skew_hot_values or {}
        self._ctr = 0
        self._dialect = dialect or get_current_dialect()
        self._enable_known_mysql_date_index_string_eq_workaround = (
            enable_known_mysql_date_index_string_eq_workaround
            if enable_known_mysql_date_index_string_eq_workaround is not None
            else self._dialect.optimizer_family() == 'mysql'
        )
        self._ablation_config = ablation_config
        # Per-dialect filtered function pools (e.g. MariaDB excludes LOG/EXP/SIN/COS/TAN)
        self._num_funs = [f for f in _NUM_SCALAR_FUNS if self._dialect.supports_function(f)]
        self._str_funs = [f for f in _STR_SCALAR_FUNS if self._dialect.supports_function(f)]

    def _is_monetdb_family(self) -> bool:
        return self._dialect.optimizer_family() == 'monetdb'

    def _distinct_keyword(self, prob: float) -> str:
        if self._is_monetdb_family():
            return ''
        return 'DISTINCT ' if random.random() < prob else ''

    def generate(self) -> Optional[str]:
        builders = {
            'SINGLE': self._build_single,
            'INNER_JOIN_2': self._build_inner_join_2,
            'IMPLICIT_CONVERSION_JOIN': self._build_implicit_conversion_join,
            'RARE_BEHAVIOR_JOIN': self._build_rare_behavior_join,
            'INNER_JOIN_3': self._build_inner_join_3,
            'SELF_JOIN': self._build_self_join,
            'CROSS_JOIN_FILTERED': self._build_cross_join_filtered,
            'CTE_WRAPPER': self._build_cte_wrapper,
            'DERIVED_TABLE': self._build_derived_table,
            'IN_SUBQUERY': self._build_in_subquery,
            'EXISTS_SUBQUERY': self._build_exists_subquery,
            'NESTED_DERIVED': self._build_nested_derived,
            'UNION_ALL': self._build_union_all,
        }

        for _ in range(_MAX_GENERATE_RETRIES):
            self._ctr = 0
            shape = None
            try:
                shape = self._choose_shape()
                sql = builders[shape]()
                if sql and self._validate_monotone_sql(sql):
                    return sql
            except Exception as e:
                print(f"[gen ERROR] shape={shape}: {e}")
                traceback.print_exc()
        return None

    def generate_dml_effect(self):
        from oracle.subset_oracle import DMLEffectSpec

        tname, cols = self._main_table()
        pk_col = next((c for c in cols if c.is_primary_key), None)
        if pk_col is None:
            return None

        where = self._where_clause([('', cols)])
        if not where:
            return None

        dml_type = random.choice(['DELETE', 'UPDATE'])
        if dml_type == 'DELETE':
            dml_sql = f"DELETE FROM {self._qi(tname)}{where}"
        else:
            candidates = [
                c for c in cols
                if not c.is_primary_key and c.data_type in _NUMERIC_FAMILY
            ]
            if not candidates:
                return None
            target = random.choice(candidates)
            delta = random.choice(['1', '-1', '2'])
            target_ref = self._qi(target.name)
            dml_sql = (
                f"UPDATE {self._qi(tname)} "
                f"SET {target_ref} = COALESCE({target_ref}, 0) + {delta}{where}"
            )

        key_select_sql = (
            f"SELECT {self._qi(pk_col.name)} FROM {self._qi(tname)}{where}"
        )
        return DMLEffectSpec(
            table_name=tname,
            dml_sql=dml_sql,
            dml_type=dml_type,
            key_select_sql=key_select_sql,
            assertion='<=',
        )

    def _choose_shape(self) -> str:
        n = len(self.tables)
        # CTE weight: 0 if dialect doesn't support CTEs, otherwise normal weight
        cte_w = 10 if self._dialect.supports_cte() else 0
        rare_join_w = 0 if self._dialect.optimizer_family() == 'monetdb' else None
        implicit_conv_w = 0 if self._is_monetdb_family() else None
        union_all_w = 0 if self._is_monetdb_family() else None
        if self._ablation_config is not None and not self._ablation_config.opt4_type_conversion:
            implicit_conv_w = 0
            rare_join_w = 0 if rare_join_w is None else rare_join_w

        if self._is_monetdb_family():
            if n == 1:
                pool = [
                    ('SINGLE', 60),
                    ('DERIVED_TABLE', 22),
                    ('CTE_WRAPPER', cte_w),
                    ('SELF_JOIN', 10),
                    ('NESTED_DERIVED', 8),
                ]
            elif n == 2:
                pool = [
                    ('SINGLE', 20),
                    ('INNER_JOIN_2', 34),
                    ('DERIVED_TABLE', 18),
                    ('CTE_WRAPPER', cte_w),
                    ('CROSS_JOIN_FILTERED', 8),
                    ('SELF_JOIN', 8),
                    ('NESTED_DERIVED', 4),
                ]
            else:
                pool = [
                    ('SINGLE', 18),
                    ('INNER_JOIN_2', 30),
                    ('INNER_JOIN_3', 20),
                    ('DERIVED_TABLE', 16),
                    ('CTE_WRAPPER', cte_w),
                    ('CROSS_JOIN_FILTERED', 8),
                    ('SELF_JOIN', 6),
                    ('NESTED_DERIVED', 2),
                ]
            pool = [(s, w) for s, w in pool if w > 0]
            shapes, weights = zip(*pool)
            return random.choices(shapes, weights=weights, k=1)[0]

        if n == 1:
            pool = [
                ('SINGLE', 34),
                ('SELF_JOIN', 18),
                ('CTE_WRAPPER', cte_w),
                ('DERIVED_TABLE', 22),
                ('IN_SUBQUERY', 10),
                ('EXISTS_SUBQUERY', 6),
                ('NESTED_DERIVED', 4),
                ('UNION_ALL', union_all_w if union_all_w is not None else 2),
            ]
        elif n == 2:
            pool = [
                ('SINGLE', 8),
                ('INNER_JOIN_2', 24),
                ('IMPLICIT_CONVERSION_JOIN', implicit_conv_w if implicit_conv_w is not None else 16),
                ('RARE_BEHAVIOR_JOIN', rare_join_w if rare_join_w is not None else 18),
                ('SELF_JOIN', 10),
                ('CROSS_JOIN_FILTERED', 10),
                ('CTE_WRAPPER', cte_w),
                ('DERIVED_TABLE', 14),
                ('IN_SUBQUERY', 14),
                ('EXISTS_SUBQUERY', 10),
                ('NESTED_DERIVED', 4),
                ('UNION_ALL', union_all_w if union_all_w is not None else 2),
            ]
        else:
            pool = [
                ('SINGLE', 6),
                ('INNER_JOIN_2', 20),
                ('IMPLICIT_CONVERSION_JOIN', implicit_conv_w if implicit_conv_w is not None else 14),
                ('RARE_BEHAVIOR_JOIN', rare_join_w if rare_join_w is not None else 16),
                ('INNER_JOIN_3', 14),
                ('SELF_JOIN', 10),
                ('CROSS_JOIN_FILTERED', 8),
                ('CTE_WRAPPER', cte_w),
                ('DERIVED_TABLE', 14),
                ('IN_SUBQUERY', 12),
                ('EXISTS_SUBQUERY', 8),
                ('NESTED_DERIVED', 4),
                ('UNION_ALL', union_all_w if union_all_w is not None else 2),
            ]
        pool = [(s, w) for s, w in pool if w > 0]
        shapes, weights = zip(*pool)
        return random.choices(shapes, weights=weights, k=1)[0]

    # ──────────────────────────────────────────
    # Query shape builders
    # ──────────────────────────────────────────

    def _build_single(self) -> str:
        tname, cols = random.choice(self.tables)
        alias = self._alias(tname)
        alias_cols = [(alias, cols)]
        distinct = self._distinct_keyword(0.2)
        sql = (
            f"SELECT {distinct}{self._select_list(alias_cols)} "
            f"FROM {self._qi(tname)} {alias}{self._where_clause(alias_cols)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_inner_join_2(self) -> str:
        t1n, t1c = self.tables[0]
        other = [t for t in self.tables if t[0] != t1n]
        t2n, t2c = random.choice(other) if other else self.tables[0]
        a1 = self._alias(t1n)
        a2 = self._alias(t2n)
        on = self._join_on(a1, t1c, a2, t2c)
        if on is None:
            return self._build_single()

        alias_cols = [(a1, t1c), (a2, t2c)]
        distinct = self._distinct_keyword(0.15)
        sql = (
            f"SELECT {distinct}{self._select_list(alias_cols)} "
            f"FROM {self._qi(t1n)} {a1} INNER JOIN {self._qi(t2n)} {a2} ON {on}"
            f"{self._where_clause(alias_cols)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_implicit_conversion_join(self) -> str:
        if len(self.tables) < 2:
            return self._build_single()

        t1n, t1c = self.tables[0]
        other = [t for t in self.tables if t[0] != t1n]
        random.shuffle(other)
        suppressed_known_pair = False

        for t2n, t2c in other:
            pair = self._pick_risky_join_pair(
                t1c,
                t2c,
                allow_known_date_index_string_eq=False,
                allow_mysql_date_string_equality=False,
            )
            if pair is None:
                if any(
                    self._is_known_date_index_string_eq(c1, c2)
                    for c1 in t1c for c2 in t2c
                ):
                    suppressed_known_pair = True
                continue

            c1, c2 = pair
            if self._is_known_date_index_string_eq(c1, c2):
                suppressed_known_pair = True
                continue

            a1 = self._alias(t1n)
            a2 = self._alias(t2n)
            alias_cols = [(a1, t1c), (a2, t2c)]
            on = f"{self._qref(a1, c1.name)} = {self._qref(a2, c2.name)}"
            sql = (
                f"SELECT /*implicit_conversion_join*/ {self._select_list(alias_cols)} "
                f"FROM {self._qi(t1n)} {a1} INNER JOIN {self._qi(t2n)} {a2} ON {on}"
                f"{self._where_clause(alias_cols)}"
            )
            return self._maybe_add_order_by(sql, alias_cols)

        if suppressed_known_pair:
            return self._build_inner_join_2()
        return self._build_inner_join_2()
    def _build_rare_behavior_join(self) -> str:
        if len(self.tables) < 2:
            return self._build_single()

        builders = [
            self._build_null_safe_join,
            self._build_row_constructor_join,
            self._build_prefix_expression_join,
            self._build_collate_join,
            self._build_enum_cross_join,
            self._build_find_in_set_join,
            self._build_like_prefix_join,
            self._build_temporal_cast_join,
            self._build_strcmp_join,
            self._build_char_padding_join,
        ]
        random.shuffle(builders)
        for builder in builders:
            sql = builder()
            if sql:
                return sql
        return self._build_inner_join_2()

    def _build_inner_join_3(self) -> str:
        if len(self.tables) < 3:
            return self._build_inner_join_2()

        t1n, t1c = self.tables[0]
        t2n, t2c = self.tables[1]
        t3n, t3c = self.tables[2]
        a1 = self._alias(t1n)
        a2 = self._alias(t2n)
        a3 = self._alias(t3n)
        on12 = self._join_on(a1, t1c, a2, t2c)
        on23 = self._join_on(a2, t2c, a3, t3c)
        if on12 is None or on23 is None:
            return self._build_inner_join_2()

        alias_cols = [(a1, t1c), (a2, t2c), (a3, t3c)]
        distinct = self._distinct_keyword(0.1)
        sql = (
            f"SELECT {distinct}{self._select_list(alias_cols)} "
            f"FROM {self._qi(t1n)} {a1} "
            f"INNER JOIN {self._qi(t2n)} {a2} ON {on12} "
            f"INNER JOIN {self._qi(t3n)} {a3} ON {on23}"
            f"{self._where_clause(alias_cols)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_self_join(self) -> str:
        tname, cols = random.choice(self.tables)
        a1 = self._alias(tname)
        a2 = self._alias(tname)
        on = self._join_on(a1, cols, a2, cols)
        if on is None:
            return self._build_single()

        alias_cols = [(a1, cols), (a2, cols)]
        distinct = self._distinct_keyword(0.12)
        sql = (
            f"SELECT {distinct}{self._select_list(alias_cols)} "
            f"FROM {self._qi(tname)} {a1} INNER JOIN {self._qi(tname)} {a2} ON {on}"
            f"{self._where_clause(alias_cols)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_cross_join_filtered(self) -> str:
        if len(self.tables) < 2:
            return self._build_self_join()

        t1n, t1c = random.choice(self.tables)
        other = [t for t in self.tables if t[0] != t1n]
        t2n, t2c = random.choice(other) if other else random.choice(self.tables)
        a1 = self._alias(t1n)
        a2 = self._alias(t2n)
        alias_cols = [(a1, t1c), (a2, t2c)]
        pair_pred = self._pair_predicate_from_alias_cols(alias_cols, require_cross_alias=True)
        if pair_pred is None:
            return self._build_inner_join_2()

        where = self._merge_where(self._where_clause(alias_cols), pair_pred)
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM {self._qi(t1n)} {a1} CROSS JOIN {self._qi(t2n)} {a2}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_cte_wrapper(self) -> str:
        tname, cols = random.choice(self.tables)
        inner_a = self._alias('c')
        inner_cols = random.sample(cols, k=min(random.randint(2, 4), len(cols)))
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in inner_cols
        )
        inner_sql = (
            f"SELECT {inner_sel} FROM {self._qi(tname)} {inner_a}"
            f"{self._where_clause([(inner_a, cols)])}"
        )

        cte_name = f"cte_{random.randint(1, 999)}"
        cte_alias = self._alias('cte')
        outer_alias_cols = [(cte_alias, inner_cols)]
        outer_sel = self._select_list_from_col_defs(cte_alias, inner_cols)
        outer_where = self._where_clause(outer_alias_cols)

        if random.random() < 0.35:
            other = [t for t in self.tables if t[0] != tname]
            if other:
                t2n, t2c = random.choice(other)
                a2 = self._alias(t2n)
                on = self._join_on_cold(cte_alias, inner_cols, a2, t2c)
                if on:
                    extra_templates = self._pick_projection_templates([(a2, t2c)], 1, min(2, len(t2c)))
                    extra_sel = self._select_list_from_templates([(a2, t2c)], extra_templates)
                    sql = (
                        f"WITH {cte_name} AS ({inner_sql}) "
                        f"SELECT {outer_sel}, {extra_sel} "
                        f"FROM {cte_name} {cte_alias} INNER JOIN {self._qi(t2n)} {a2} ON {on}{outer_where}"
                    )
                    return self._maybe_add_order_by(sql, outer_alias_cols + [(a2, t2c)])

        sql = f"WITH {cte_name} AS ({inner_sql}) SELECT {outer_sel} FROM {cte_name} {cte_alias}{outer_where}"
        return self._maybe_add_order_by(sql, outer_alias_cols)

    def _build_derived_table(self) -> str:
        tname, cols = random.choice(self.tables)
        inner_a = self._alias('i')
        inner_n = min(random.randint(2, 4), len(cols))
        inner_cols = random.sample(cols, k=inner_n)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in inner_cols
        )
        inner_sql = (
            f"SELECT {inner_sel} FROM {self._qi(tname)} {inner_a}"
            f"{self._where_clause([(inner_a, cols)])}"
        )

        sub_a = self._alias('s')
        outer_alias_cols = [(sub_a, inner_cols)]
        outer_sel = self._select_list_from_col_defs(sub_a, inner_cols)
        outer_where = self._where_clause(outer_alias_cols)

        if random.random() < 0.35:
            other = [t for t in self.tables if t[0] != tname]
            if other:
                t2n, t2c = random.choice(other)
                a2 = self._alias(t2n)
                on = self._join_on_cold(sub_a, inner_cols, a2, t2c)
                if on:
                    extra_templates = self._pick_projection_templates([(a2, t2c)], 1, min(2, len(t2c)))
                    extra_sel = self._select_list_from_templates([(a2, t2c)], extra_templates)
                    sql = (
                        f"SELECT {outer_sel}, {extra_sel} "
                        f"FROM ({inner_sql}) {sub_a} INNER JOIN {self._qi(t2n)} {a2} ON {on}{outer_where}"
                    )
                    return self._maybe_add_order_by(sql, outer_alias_cols + [(a2, t2c)])

        sql = f"SELECT {outer_sel} FROM ({inner_sql}) {sub_a}{outer_where}"
        return self._maybe_add_order_by(sql, outer_alias_cols)

    def _build_in_subquery(self) -> str:
        tname, cols = self.tables[0]
        alias = self._alias(tname)
        candidates = [
            c for c in cols
            if not c.is_primary_key
            and (c.data_type in _INT_FAMILY or c.data_type in _TEMPORAL_FAMILY or self._is_string_like_type(c.data_type))
        ]
        if not candidates:
            return self._build_single()
        main_col = random.choice(candidates)

        st_name, st_cols = random.choice(self.tables)
        compat = [
            c for c in st_cols
            if self._same_comparable_type(
                main_col,
                c,
                allow_known_date_index_string_eq=False,
                allow_mysql_date_string_equality=False,
            )
            and not self._is_known_mysql_year_string_in_subquery_risk(main_col, c)
        ]
        if not compat:
            return self._build_single()
        sub_col = random.choice(compat)
        sq_a = self._alias('sq')
        subquery = (
            f"SELECT {self._qref(sq_a, sub_col.name)} "
            f"FROM {self._qi(st_name)} {sq_a}{self._where_clause([(sq_a, st_cols)])}"
        )

        alias_cols = [(alias, cols)]
        where = self._merge_where(
            self._where_clause(alias_cols),
            f"{self._qref(alias, main_col.name)} IN ({subquery})",
        )
        comment = " /*implicit_conversion_in*/" if self._is_risky_cross_type_pair(main_col, sub_col) else ''
        sql = f"SELECT{comment} {self._select_list(alias_cols)} FROM {self._qi(tname)} {alias}{where}"
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_exists_subquery(self) -> str:
        tname, cols = self.tables[0]
        alias = self._alias(tname)
        other = [t for t in self.tables if t[0] != tname]
        st_name, st_cols = random.choice(other) if other else self.tables[0]
        sq_a = self._alias('ex')

        jc_main = self._pick_join_col(cols)
        jc_sub = self._pick_compat_col(
            st_cols,
            jc_main,
            allow_known_date_index_string_eq=False,
            allow_mysql_date_string_equality=False,
        )
        if jc_main is None or jc_sub is None:
            return self._build_single()

        corr = f"{self._qref(alias, jc_main.name)} = {self._qref(sq_a, jc_sub.name)}"
        sub_where = self._merge_where(self._where_clause([(sq_a, st_cols)]), corr)
        exists_pred = f"EXISTS (SELECT 1 FROM {self._qi(st_name)} {sq_a}{sub_where})"

        alias_cols = [(alias, cols)]
        where = self._merge_where(self._where_clause(alias_cols), exists_pred)
        sql = f"SELECT {self._select_list(alias_cols)} FROM {self._qi(tname)} {alias}{where}"
        if self._is_known_mysql_exists_order_by_year_string_risk(jc_main, jc_sub):
            return sql
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_nested_derived(self) -> str:
        tname, cols = random.choice(self.tables)

        i1_a = self._alias('i1')
        i1_cols = random.sample(cols, k=min(random.randint(2, 4), len(cols)))
        i1_sel = ', '.join(
            f"{self._qref(i1_a, c.name)} AS {self._qi(c.name)}" for c in i1_cols
        )
        i1_sql = (
            f"SELECT {i1_sel} FROM {self._qi(tname)} {i1_a}"
            f"{self._where_clause([(i1_a, cols)])}"
        )

        mid_a = self._alias('m')
        mid_cols = random.sample(i1_cols, k=min(random.randint(1, len(i1_cols)), len(i1_cols)))
        mid_sel = ', '.join(
            f"{self._qref(mid_a, c.name)} AS {self._qi(c.name)}" for c in mid_cols
        )
        mid_sql = (
            f"SELECT {mid_sel} FROM ({i1_sql}) {mid_a}"
            f"{self._where_clause([(mid_a, i1_cols)])}"
        )

        out_a = self._alias('o')
        out_alias_cols = [(out_a, mid_cols)]
        sql = (
            f"SELECT {self._select_list_from_col_defs(out_a, mid_cols)} "
            f"FROM ({mid_sql}) {out_a}{self._where_clause(out_alias_cols)}"
        )
        return self._maybe_add_order_by(sql, out_alias_cols)

    def _build_union_all(self) -> str:
        if len(self.tables) >= 2 and random.random() < 0.45:
            return self._build_union_all_join()
        return self._build_union_all_single()

    def _build_union_all_single(self) -> str:
        tname, cols = random.choice(self.tables)
        a1 = self._alias(tname)
        a2 = self._alias(tname)
        alias_cols_1 = [(a1, cols)]
        alias_cols_2 = [(a2, cols)]
        templates = self._pick_projection_templates(alias_cols_1)
        q1 = (
            f"SELECT {self._select_list_from_templates(alias_cols_1, templates, mode='union')} "
            f"FROM {self._qi(tname)} {a1}{self._where_clause(alias_cols_1)}"
        )
        q2 = (
            f"SELECT {self._select_list_from_templates(alias_cols_2, templates, mode='union')} "
            f"FROM {self._qi(tname)} {a2}{self._where_clause(alias_cols_2)}"
        )
        return f"{q1} UNION ALL {q2}"

    def _build_union_all_join(self) -> str:
        t1n, t1c = self.tables[0]
        other = [t for t in self.tables if t[0] != t1n]
        t2n, t2c = random.choice(other) if other else self.tables[0]

        a11 = self._alias(t1n)
        a12 = self._alias(t2n)
        a21 = self._alias(t1n)
        a22 = self._alias(t2n)
        on1 = self._join_on(a11, t1c, a12, t2c)
        on2 = self._join_on(a21, t1c, a22, t2c)
        if on1 is None or on2 is None:
            return self._build_union_all_single()

        alias_cols_1 = [(a11, t1c), (a12, t2c)]
        alias_cols_2 = [(a21, t1c), (a22, t2c)]
        templates = self._pick_projection_templates(alias_cols_1)

        q1 = (
            f"SELECT {self._select_list_from_templates(alias_cols_1, templates, mode='union')} "
            f"FROM {self._qi(t1n)} {a11} INNER JOIN {self._qi(t2n)} {a12} ON {on1}"
            f"{self._where_clause(alias_cols_1)}"
        )
        q2 = (
            f"SELECT {self._select_list_from_templates(alias_cols_2, templates, mode='union')} "
            f"FROM {self._qi(t1n)} {a21} INNER JOIN {self._qi(t2n)} {a22} ON {on2}"
            f"{self._where_clause(alias_cols_2)}"
        )
        return f"{q1} UNION ALL {q2}"

    def _render_join_sql(self,
                         t1n: str, t1c: List[Any], t2n: str, t2c: List[Any],
                         on: str, comment: str) -> str:
        a1 = self._alias(t1n)
        a2 = self._alias(t2n)
        alias_cols = [(a1, t1c), (a2, t2c)]
        on_sql = on.format(a1=a1, a2=a2)
        sql = (
            f"SELECT /*{comment}*/ {self._select_list(alias_cols)} "
            f"FROM {self._qi(t1n)} {a1} INNER JOIN {self._qi(t2n)} {a2} ON {on_sql}"
            f"{self._where_clause(alias_cols)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _iter_table_pairs(self) -> List[Tuple[str, List[Any], str, List[Any]]]:
        pairs: List[Tuple[str, List[Any], str, List[Any]]] = []
        for i, (t1n, t1c) in enumerate(self.tables):
            for j, (t2n, t2c) in enumerate(self.tables):
                if i == j:
                    continue
                pairs.append((t1n, t1c, t2n, t2c))
        random.shuffle(pairs)
        return pairs

    def _build_null_safe_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            c1 = self._pick_join_col(t1c)
            c2 = self._pick_compat_col(
                t2c,
                c1,
                allow_known_date_index_string_eq=False,
                allow_mysql_date_string_equality=False,
            )
            if c1 is None or c2 is None:
                continue
            on = "{a1}.`" + c1.name + "` <=> {a2}.`" + c2.name + "`"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_null_safe_join')
        return None

    def _build_row_constructor_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            pairs: List[Tuple[Any, Any]] = []
            for c1 in t1c:
                c2 = self._pick_compat_col(
                    t2c,
                    c1,
                    allow_known_date_index_string_eq=False,
                    allow_mysql_date_string_equality=False,
                )
                if c2 is not None:
                    pairs.append((c1, c2))
            unique_pairs: List[Tuple[Any, Any]] = []
            seen = set()
            for c1, c2 in pairs:
                key = (c1.name, c2.name)
                if key not in seen:
                    seen.add(key)
                    unique_pairs.append((c1, c2))
            if len(unique_pairs) < 2:
                continue
            left, right = random.sample(unique_pairs, 2)
            on = (
                f"({{a1}}.`{left[0].name}`, {{a1}}.`{right[0].name}`) = "
                f"({{a2}}.`{left[1].name}`, {{a2}}.`{right[1].name}`)"
            )
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_row_constructor_join')
        return None

    def _build_prefix_expression_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            lefts = [c for c in t1c if self._is_string_like_type(c.data_type)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not lefts or not rights:
                continue
            c1 = random.choice(lefts)
            c2 = random.choice(rights)
            width = random.choice([4, 6, 8, 12])
            on = f"LEFT({{a1}}.`{c1.name}`, {width}) = {{a2}}.`{c2.name}`"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_prefix_expression_join')
        return None

    def _build_collate_join(self) -> Optional[str]:
        collations = ['utf8mb4_bin', 'utf8mb4_general_ci', 'utf8mb4_unicode_ci']
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            lefts = [c for c in t1c if self._is_string_like_type(c.data_type)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not lefts or not rights:
                continue
            c1 = random.choice(lefts)
            c2 = random.choice(rights)
            coll = random.choice(collations)
            on = f"{{a1}}.`{c1.name}` COLLATE {coll} = {{a2}}.`{c2.name}`"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_collate_join')
        return None

    def _build_enum_cross_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            enums = [c for c in t1c if self._is_enum_col(c)]
            if not enums:
                continue
            enum_col = random.choice(enums)
            string_targets = [c for c in t2c if self._is_string_like_type(c.data_type)]
            int_targets = [c for c in t2c if c.data_type in _INT_FAMILY]
            choices: List[str] = []
            if string_targets:
                tgt = random.choice(string_targets)
                choices.append(f"{{a1}}.`{enum_col.name}` = {{a2}}.`{tgt.name}`")
            if int_targets:
                tgt = random.choice(int_targets)
                choices.append(f"{{a1}}.`{enum_col.name}` + 0 = {{a2}}.`{tgt.name}`")
            if not choices:
                continue
            return self._render_join_sql(t1n, t1c, t2n, t2c, random.choice(choices), 'rare_enum_cross_join')
        return None

    def _build_find_in_set_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            sets = [c for c in t1c if self._is_set_col(c)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not sets or not rights:
                continue
            c1 = random.choice(sets)
            c2 = random.choice(rights)
            on = f"FIND_IN_SET({{a2}}.`{c2.name}`, {{a1}}.`{c1.name}`) > 0"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_find_in_set_join')
        return None

    def _build_like_prefix_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            lefts = [c for c in t1c if self._is_string_like_type(c.data_type)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not lefts or not rights:
                continue
            c1 = random.choice(lefts)
            c2 = random.choice(rights)
            on = f"{{a1}}.`{c1.name}` LIKE CONCAT({{a2}}.`{c2.name}`, '%')"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_like_prefix_join')
        return None

    def _build_temporal_cast_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            lefts = [c for c in t1c if self._is_timestampish_col(c)]
            rights = [c for c in t2c if c.data_type == 'DATE']
            if not lefts or not rights:
                continue
            c1 = random.choice(lefts)
            c2 = random.choice(rights)
            on = random.choice([
                f"CAST({{a1}}.`{c1.name}` AS DATE) = {{a2}}.`{c2.name}`",
                f"DATE({{a1}}.`{c1.name}`) = {{a2}}.`{c2.name}`",
            ])
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_temporal_cast_join')
        return None

    def _build_strcmp_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            lefts = [c for c in t1c if self._is_string_like_type(c.data_type)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not lefts or not rights:
                continue
            c1 = random.choice(lefts)
            c2 = random.choice(rights)
            on = f"STRCMP({{a1}}.`{c1.name}`, {{a2}}.`{c2.name}`) = 0"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_strcmp_join')
        return None

    def _build_char_padding_join(self) -> Optional[str]:
        for t1n, t1c, t2n, t2c in self._iter_table_pairs():
            chars = [c for c in t1c if self._is_char_col(c)]
            rights = [c for c in t2c if self._is_string_like_type(c.data_type)]
            if not chars or not rights:
                continue
            c1 = random.choice(chars)
            c2 = random.choice(rights)
            on = f"RTRIM({{a1}}.`{c1.name}`) = {{a2}}.`{c2.name}`"
            return self._render_join_sql(t1n, t1c, t2n, t2c, on, 'rare_char_padding_join')
        return None

    # ──────────────────────────────────────────
    # Projection helpers
    # ──────────────────────────────────────────

    def _pick_projection_templates(self, alias_cols: List[Tuple[str, List[Any]]],
                                   min_cols: int = _SELECT_MIN_COLS,
                                   max_cols: int = _SELECT_MAX_COLS) -> List[Tuple[int, Any]]:
        refs = [
            (idx, c)
            for idx, (_, cols) in enumerate(alias_cols)
            for c in cols
            if self._projection_col_allowed(c)
        ]
        if not refs:
            return []
        k = min(random.randint(min_cols, max_cols), len(refs))
        chosen = random.sample(refs, k=k)
        out: List[Tuple[int, Any]] = []
        seen = set()
        for idx, col in chosen:
            key = (idx, col.name)
            if key not in seen:
                seen.add(key)
                out.append((idx, col))
        return out

    def _projection_col_allowed(self, col: Any) -> bool:
        if col.data_type != 'OPAQUE':
            return True
        return self._dialect.optimizer_family() != 'oceanbase'

    def _select_list(self, alias_cols: List[Tuple[str, List[Any]]],
                     allow_scalar_fn: bool = True) -> str:
        templates = self._pick_projection_templates(alias_cols)
        return self._select_list_from_templates(alias_cols, templates, allow_scalar_fn=allow_scalar_fn)

    def _select_list_from_col_defs(self, alias: str, cols: List[Any]) -> str:
        alias_cols = [(alias, cols)]
        templates = self._pick_projection_templates(alias_cols, 1, max(1, len(cols)))
        return self._select_list_from_templates(alias_cols, templates)

    def _select_list_from_templates(self, alias_cols: List[Tuple[str, List[Any]]],
                                    templates: List[Tuple[int, Any]],
                                    mode: str = 'default',
                                    allow_scalar_fn: bool = True) -> str:
        exprs = []
        for idx, col in templates:
            alias = alias_cols[idx][0]
            exprs.append(self._col_expr(alias, col, alias_cols, allow_scalar_fn, mode))
        return ', '.join(exprs) if exprs else '*'

    def _col_expr(self, alias: str, col: Any,
                  alias_cols: Optional[List[Tuple[str, List[Any]]]] = None,
                  allow_scalar_fn: bool = True,
                  mode: str = 'default') -> str:
        base = self._qref(alias, col.name)
        dt = col.data_type

        if mode == 'union':
            return self._union_compatible_expr(alias, col)

        if not allow_scalar_fn or random.random() < 0.4:
            return base

        r = random.random()
        alias_cols = alias_cols or [(alias, [col])]

        if dt in _NUMERIC_FAMILY:
            other = self._pick_compatible_ref(alias_cols, col, alias)
            if r < 0.18 and self._num_funs:
                return f"{self._fn(random.choice(self._num_funs))}({base})"
            if r < 0.34:
                return f"({base} {random.choice(_ARITH_OPS)} {random.randint(1, 10)})"
            if r < 0.50 and other:
                other_ref = self._qref(other[0], other[1].name)
                return f"(COALESCE({base}, 0) + COALESCE({other_ref}, 0))"
            if r < 0.66:
                return f"COALESCE({base}, {random.randint(-8, 8)})"
            if r < 0.82:
                threshold = random.randint(-20, 20)
                return f"(CASE WHEN {base} >= {threshold} THEN {base} ELSE {threshold} END)"
            return base

        if self._is_string_like_type(dt):
            other = self._pick_compatible_ref(alias_cols, col, alias)
            empty_str = self._lit('', 'VARCHAR')
            if r < 0.18 and self._str_funs:
                return f"{self._fn(random.choice(self._str_funs))}({base})"
            if r < 0.34:
                return f"COALESCE({base}, {empty_str})"
            if r < 0.50:
                return f"{self._fn('CONCAT')}({base}, {empty_str})"
            if r < 0.66 and other:
                other_ref = self._qref(other[0], other[1].name)
                return (
                    f"{self._fn('CONCAT')}"
                    f"(COALESCE({base}, {empty_str}), COALESCE({other_ref}, {empty_str}))"
                )
            if r < 0.82:
                return (
                    f"(CASE WHEN {base} IS NULL"
                    f" THEN {self._lit('missing', 'VARCHAR')} ELSE {base} END)"
                )
            return base

        if dt == 'DATE':
            if r < 0.35:
                return f"COALESCE({base}, {self._lit('2023-01-01', 'DATE')})"
            return base

        if dt == 'OPAQUE':
            return base

        return base

    def _union_compatible_expr(self, alias: str, col: Any) -> str:
        base = self._qref(alias, col.name)
        dt = col.data_type
        r = random.random()
        if dt in _NUMERIC_FAMILY:
            if r < 0.35:
                return base
            if r < 0.60:
                return f"COALESCE({base}, 0)"
            if r < 0.80:
                return f"{self._fn('ABS')}({base})"
            return f"({base} + {random.randint(0, 5)})"
        if self._is_string_like_type(dt):
            empty_str = self._lit('', 'VARCHAR')
            if r < 0.40:
                return base
            if r < 0.65:
                return f"COALESCE({base}, {empty_str})"
            if r < 0.85:
                return f"{self._fn('UPPER')}({base})"
            return f"{self._fn('CONCAT')}({base}, {empty_str})"
        if dt in _TEMPORAL_FAMILY:
            if r < 0.5:
                return base
            if dt == 'TIME':
                return f"COALESCE({base}, '00:00:00')"
            if dt in ('DATETIME', 'TIMESTAMP'):
                return f"COALESCE({base}, {self._lit('2023-01-01 00:00:00', 'DATETIME')})"
            return f"COALESCE({base}, {self._lit('2023-01-01', 'DATE')})"
        return base

    # ──────────────────────────────────────────
    # WHERE / predicate helpers
    # ──────────────────────────────────────────

    def _where_clause(self,
                      alias_cols: List[Tuple[str, List[Any]]],
                      max_preds: int = _WHERE_MAX_PREDS) -> str:
        if random.random() < 0.12:
            return ''

        refs = self._refs(alias_cols, prefer_non_pk=True)
        if not refs:
            return ''

        target = random.randint(1, min(max_preds, max(1, len(refs))))
        preds: List[str] = []
        attempts = 0
        while len(preds) < target and attempts < target * 6:
            attempts += 1
            pred = None
            if len(alias_cols) > 1 and random.random() < 0.35:
                pred = self._pair_predicate_from_alias_cols(alias_cols)
            if pred is None:
                a, c = random.choice(refs)
                pred = self._predicate(a, c, alias_cols)
            if pred and pred not in preds:
                if self._would_form_known_mysql_null_contradiction(preds, pred):
                    continue
                preds.append(pred)

        if not preds:
            return ''
        return ' WHERE ' + self._combine_preds(preds)

    def _combine_preds(self, preds: List[str]) -> str:
        if len(preds) == 1:
            return preds[0]

        result = [preds[0]]
        for pred in preds[1:]:
            if random.random() < 0.2 and result:
                prev = result.pop()
                result.append(f"({prev} OR {pred})")
            else:
                result.append(pred)
        return ' AND '.join(result)

    def _predicate(self, alias: str, col: Any,
                   alias_cols: Optional[List[Tuple[str, List[Any]]]] = None) -> Optional[str]:
        ref = self._qref(alias, col.name)
        dt = col.data_type
        r = random.random()
        alias_cols = alias_cols or [(alias, [col])]

        if dt in _TEMPORAL_FAMILY:
            if self._is_monetdb_family():
                return f"{ref} IS NOT NULL" if r < 0.65 else f"{ref} IS NULL"
            other = self._pick_compatible_ref(alias_cols, col, alias)
            if other and r < 0.35 and not self._is_known_mysql_date_string_equality_risk(col, other[1]):
                op = '=' if self._is_risky_cross_type_pair(col, other[1]) else '>='
                return f"{ref} {op} {self._qref(other[0], other[1].name)}"
            return f"{ref} IS NOT NULL" if r < 0.65 else f"{ref} IS NULL"
        
        if dt == 'OPAQUE':
            return f"{ref} IS NOT NULL" if r < 0.5 else f"{ref} IS NULL"

        hot = self._hot(col)

        if dt in _INT_FAMILY:
            v1 = hot or str(random.randint(-100, 100))
            v2 = str(int(float(v1)) + random.randint(1, 20))
            other = self._pick_compatible_ref(alias_cols, col, alias)
            if r < 0.14:
                return f"{ref} = {v1}"
            if r < 0.28:
                return f"{ref} >= {v1}"
            if r < 0.40:
                return f"{ref} <= {v1}"
            if r < 0.52:
                return f"{ref} BETWEEN {v1} AND {v2}"
            if r < 0.62 and other:
                op = '=' if self._is_risky_cross_type_pair(col, other[1]) else random.choice(['=', '>=', '<='])
                return f"{ref} {op} {self._qref(other[0], other[1].name)}"
            if r < 0.72:
                return f"{self._fn('ABS')}({ref}) >= {abs(int(float(v1)))}"
            if r < 0.80:
                return f"COALESCE({ref}, 0) >= {v1}"
            if r < 0.88:
                return f"{ref} IS NULL"
            if r < 0.94:
                return f"{ref} IS NOT NULL"
            lits = [str(random.randint(-200, 200)) for _ in range(random.randint(2, 5))]
            return f"{ref} IN ({', '.join(lits)})"

        if self._is_string_like_type(dt):
            v = hot or self._lit(f"val{random.randint(0, 99)}", 'VARCHAR')
            inner = v.strip("'")
            prefix = (inner[:2] if len(inner) >= 2 else inner) or 'v'
            other = self._pick_compatible_ref(alias_cols, col, alias)
            empty_str = self._lit('', 'VARCHAR')
            if r < 0.18:
                return f"{ref} = {v}"
            if r < 0.34:
                return f"{ref} LIKE '{prefix}%'"
            if r < 0.46:
                return f"{ref} >= {v}"
            if r < 0.58 and other and not self._is_known_mysql_date_string_equality_risk(col, other[1]):
                return f"{ref} = {self._qref(other[0], other[1].name)}"
            if r < 0.70:
                return f"{self._fn('LENGTH')}(COALESCE({ref}, {empty_str})) >= {random.randint(0, 8)}"
            if r < 0.80:
                return f"COALESCE({ref}, {empty_str}) >= {v}"
            if r < 0.88:
                return f"{ref} IS NULL"
            if r < 0.94:
                return f"{ref} IS NOT NULL"
            lits = [
                self._lit(f"w{random.randint(0, 99)}", 'VARCHAR')
                for _ in range(random.randint(2, 4))
            ]
            return f"{ref} IN ({', '.join(lits)})"

        if dt in _NUMERIC_FAMILY:
            try:
                v1 = hot or f"{random.uniform(-100, 100):.3f}"
                v2 = f"{float(v1) + random.uniform(0.1, 10):.3f}"
            except (ValueError, TypeError):
                v1 = '0.0'
                v2 = '10.0'
            other = self._pick_compatible_ref(alias_cols, col, alias)
            if r < 0.22:
                return f"{ref} >= {v1}"
            if r < 0.40:
                return f"{ref} <= {v1}"
            if r < 0.56:
                return f"{ref} BETWEEN {v1} AND {v2}"
            if r < 0.68 and other:
                op = '=' if self._is_risky_cross_type_pair(col, other[1]) else random.choice(['>=', '<='])
                return f"{ref} {op} {self._qref(other[0], other[1].name)}"
            if r < 0.80:
                return f"{self._fn('ABS')}({ref}) >= {abs(float(v1)):.3f}"
            if r < 0.90:
                return f"{ref} IS NOT NULL"
            return f"{ref} IS NULL"

        return f"{ref} IS NOT NULL" if r < 0.5 else f"{ref} IS NULL"

    # ──────────────────────────────────────────
    # JOIN helpers
    # ──────────────────────────────────────────

    def _join_on(self, a1: str, cols1: List[Any], a2: str, cols2: List[Any]) -> Optional[str]:
        c1 = self._pick_join_col(cols1)
        c2 = self._pick_compat_col(
            cols2,
            c1,
            allow_known_date_index_string_eq=False,
            allow_mysql_date_string_equality=False,
        )
        if c1 is None or c2 is None:
            return None

        parts = [f"{self._qref(a1, c1.name)} = {self._qref(a2, c2.name)}"]
        extra = self._pair_predicate((a1, c1), (a2, c2), allow_equality=False, prefer_nontrivial=True)
        if extra and random.random() < 0.35:
            parts.append(extra)
        return ' AND '.join(parts)

    def _join_on_cold(self, sub_alias: str, sub_cols: List[Any],
                      a2: str, cols2: List[Any]) -> Optional[str]:
        return self._join_on(sub_alias, sub_cols, a2, cols2)

    def _pick_join_col(self, cols: List[Any]) -> Optional[Any]:
        safe = [c for c in cols if c.data_type not in _NO_JOIN_TYPES]
        if self._is_monetdb_family():
            non_temporal = [c for c in safe if c.data_type not in _TEMPORAL_FAMILY]
            if non_temporal:
                safe = non_temporal
        return random.choice(safe) if safe else None

    def _pick_compat_col(self,
                         cols: List[Any],
                         ref: Optional[Any],
                         allow_known_date_index_string_eq: bool = True,
                         allow_mysql_date_string_equality: bool = True) -> Optional[Any]:
        if ref is None:
            return None
        exact = [c for c in cols if c.data_type == ref.data_type]
        if exact:
            if random.random() < 0.55:
                return random.choice(exact)
        if ref.data_type in _INT_FAMILY:
            loose = [c for c in cols if c.data_type in _INT_FAMILY]
            if loose:
                if random.random() < 0.55:
                    return random.choice(loose)
        if ref.data_type in _TEMPORAL_FAMILY:
            if self._is_monetdb_family():
                exact_temporal = [c for c in cols if c.data_type == ref.data_type]
                return random.choice(exact_temporal) if exact_temporal else None
            loose = [c for c in cols if c.data_type in _TEMPORAL_FAMILY]
            if loose:
                if random.random() < 0.55:
                    return random.choice(loose)
        if self._is_string_like_type(ref.data_type):
            loose = [c for c in cols if self._is_string_like_type(c.data_type)]
            if loose:
                if random.random() < 0.55:
                    return random.choice(loose)
        risky = [
            c for c in cols
            if self._is_risky_cross_type_pair(ref, c)
            and (
                allow_known_date_index_string_eq
                or not self._is_known_date_index_string_eq(ref, c)
            )
            and (
                allow_mysql_date_string_equality
                or not self._is_known_mysql_date_string_equality_risk(ref, c)
            )
        ]
        if risky and random.random() < 0.75:
            return random.choice(risky)
        compat = [
            c for c in cols
            if self._pairwise_type_compatible(ref, c)
            and (
                allow_known_date_index_string_eq
                or not self._is_known_date_index_string_eq(ref, c)
            )
            and (
                allow_mysql_date_string_equality
                or not self._is_known_mysql_date_string_equality_risk(ref, c)
            )
        ]
        return random.choice(compat) if compat else None

    def _pick_risky_join_pair(self,
                              cols1: List[Any],
                              cols2: List[Any],
                              allow_known_date_index_string_eq: bool = True,
                              allow_mysql_date_string_equality: bool = True) -> Optional[Tuple[Any, Any]]:
        pairs = [
            (c1, c2) for c1 in cols1 for c2 in cols2
            if self._is_risky_cross_type_pair(c1, c2)
            and (
                allow_known_date_index_string_eq
                or not self._is_known_date_index_string_eq(c1, c2)
            )
            and (
                allow_mysql_date_string_equality
                or not self._is_known_mysql_date_string_equality_risk(c1, c2)
            )
        ]
        return random.choice(pairs) if pairs else None

    def _pair_predicate_from_alias_cols(self,
                                        alias_cols: List[Tuple[str, List[Any]]],
                                        require_cross_alias: bool = False) -> Optional[str]:
        refs = self._refs(alias_cols, prefer_non_pk=False)
        if len(refs) < 2:
            return None

        for _ in range(12):
            left = random.choice(refs)
            compat = [
                right for right in refs
                if right != left
                and self._pairwise_type_compatible(left[1], right[1])
                and (not require_cross_alias or left[0] != right[0])
            ]
            if not compat:
                continue
            pred = self._pair_predicate(left, random.choice(compat))
            if pred:
                return pred
        return None

    def _pair_predicate(self,
                        left: Tuple[str, Any],
                        right: Tuple[str, Any],
                        allow_equality: bool = True,
                        prefer_nontrivial: bool = False) -> Optional[str]:
        a1, c1 = left
        a2, c2 = right
        if not self._pairwise_type_compatible(c1, c2):
            return None

        ref1 = self._qref(a1, c1.name)
        ref2 = self._qref(a2, c2.name)
        dt = c1.data_type

        if self._is_known_date_index_string_eq(c1, c2):
            return None
        if self._is_known_mysql_date_string_equality_risk(c1, c2):
            return None

        if self._is_risky_cross_type_pair(c1, c2):
            if not allow_equality:
                return None
            return f"{ref1} = {ref2}"

        if dt in _NUMERIC_FAMILY or dt in _TEMPORAL_FAMILY:
            ops = ['>=', '<='] if prefer_nontrivial else ['=', '>=', '<=']
            if not allow_equality and '=' in ops:
                ops.remove('=')
            return f"{ref1} {random.choice(ops)} {ref2}"

        if self._is_string_like_type(dt):
            if allow_equality and not prefer_nontrivial:
                return f"{ref1} = {ref2}"
            empty_str = self._lit('', 'VARCHAR')
            return f"COALESCE({ref1}, {empty_str}) >= COALESCE({ref2}, {empty_str})"

        return None

    def _refs(self, alias_cols: List[Tuple[str, List[Any]]],
              prefer_non_pk: bool = True) -> List[Tuple[str, Any]]:
        refs = [(a, c) for a, cols in alias_cols for c in cols if not (prefer_non_pk and c.is_primary_key)]
        if refs:
            return refs
        return [(a, c) for a, cols in alias_cols for c in cols]

    def _pairwise_type_compatible(self, c1: Any, c2: Any) -> bool:
        if c1.data_type == 'OPAQUE' or c2.data_type == 'OPAQUE':
            return False
        if c1.data_type == c2.data_type:
            return True
        if self._is_monetdb_family() and (c1.data_type in _TEMPORAL_FAMILY or c2.data_type in _TEMPORAL_FAMILY):
            return False
        if c1.data_type in _TEMPORAL_FAMILY and c2.data_type in _TEMPORAL_FAMILY:
            return True
        if self._is_string_like_type(c1.data_type) and self._is_string_like_type(c2.data_type):
            return True
        if self._is_known_date_index_string_eq(c1, c2):
            return False
        if self._is_risky_cross_type_pair(c1, c2):
            return True
        return c1.data_type in _NUMERIC_FAMILY and c2.data_type in _NUMERIC_FAMILY

    def _same_comparable_type(self,
                              c1: Any,
                              c2: Any,
                              allow_known_date_index_string_eq: bool = True,
                              allow_mysql_date_string_equality: bool = True) -> bool:
        return (
            c1.data_type == c2.data_type
            or (c1.data_type in _INT_FAMILY and c2.data_type in _INT_FAMILY)
            or (
                c1.data_type in _TEMPORAL_FAMILY and c2.data_type in _TEMPORAL_FAMILY
                and not self._is_monetdb_family()
            )
            or (self._is_string_like_type(c1.data_type) and self._is_string_like_type(c2.data_type))
            or (
                self._is_risky_cross_type_pair(c1, c2)
                and (
                    allow_known_date_index_string_eq
                    or not self._is_known_date_index_string_eq(c1, c2)
                )
                and (
                    allow_mysql_date_string_equality
                    or not self._is_known_mysql_date_string_equality_risk(c1, c2)
                )
            )
        )

    def _pick_compatible_ref(self,
                             alias_cols: List[Tuple[str, List[Any]]],
                             ref_col: Any,
                             preferred_alias: Optional[str] = None) -> Optional[Tuple[str, Any]]:
        refs: List[Tuple[str, Any]] = []
        for alias, cols in alias_cols:
            for col in cols:
                if alias == preferred_alias and col.name == ref_col.name:
                    continue
                if self._pairwise_type_compatible(ref_col, col):
                    refs.append((alias, col))
        if not refs:
            return None

        cross_alias = [r for r in refs if preferred_alias is not None and r[0] != preferred_alias]
        if cross_alias and random.random() < 0.7:
            return random.choice(cross_alias)
        return random.choice(refs)

    def _merge_where(self, where_sql: str, predicate: str) -> str:
        if not predicate:
            return where_sql
        if 'WHERE' in where_sql.upper():
            return f"{where_sql} AND {predicate}"
        return f" WHERE {predicate}"

    def _maybe_add_order_by(self, sql: str,
                            alias_cols: List[Tuple[str, List[Any]]],
                            prob: float = _ORDER_BY_PROB) -> str:
        if random.random() >= prob:
            return sql
        expr = self._order_by_expr(alias_cols)
        if not expr:
            return sql
        direction = 'DESC' if random.random() < 0.5 else 'ASC'
        return f"{sql} ORDER BY {expr} {direction}"

    def _order_by_expr(self, alias_cols: List[Tuple[str, List[Any]]]) -> Optional[str]:
        refs = self._refs(alias_cols, prefer_non_pk=False)
        if not refs:
            return None
        alias, col = random.choice(refs)
        ref = self._qref(alias, col.name)
        dt = col.data_type
        r = random.random()
        if dt in _NUMERIC_FAMILY:
            if r < 0.45:
                return ref
            if r < 0.75:
                return f"{self._fn('ABS')}({ref})"
            return f"COALESCE({ref}, 0)"
        if self._is_string_like_type(dt):
            if r < 0.50:
                return ref
            if r < 0.80:
                return f"{self._fn('LENGTH')}(COALESCE({ref}, {self._lit('', 'VARCHAR')}))"
            return f"{self._fn('UPPER')}({ref})"
        if dt in _TEMPORAL_FAMILY:
            if r < 0.60:
                return ref
            if dt == 'TIME':
                return f"COALESCE({ref}, '00:00:00')"
            if dt in ('DATETIME', 'TIMESTAMP'):
                return f"COALESCE({ref}, {self._lit('2023-01-01 00:00:00', 'DATETIME')})"
            return f"COALESCE({ref}, {self._lit('2023-01-01', 'DATE')})"
        return ref

    # ──────────────────────────────────────────
    # Validation
    # ──────────────────────────────────────────

    def _validate_monotone_sql(self, sql: str) -> bool:
        normalized = re.sub(r'\s+', ' ', sql.upper()).strip()
        if not normalized:
            return False

        forbidden = [
            r'\bGROUP\s+BY\b',
            r'\bHAVING\b',
            r'\bLIMIT\b',
            r'\bINTERSECT\b',
            r'\bEXCEPT\b',
            r'\bLEFT\s+JOIN\b',
            r'\bRIGHT\s+JOIN\b',
            r'\bFULL\s+JOIN\b',
            r'\bFOR\s+UPDATE\b',
            r'\bNOT\s+IN\b',
            r'\bNOT\s+EXISTS\b',
            r'\bNOT\s+BETWEEN\b',
            r'\bRECURSIVE\b',
            r'\bOVER\s*\(',
            r'\bANY\s*\(',
            r'\bALL\s*\(',
            r'\bON\s+1\s*=\s*1\b',
        ]
        if any(re.search(pat, normalized) for pat in forbidden):
            return False

        stripped_union = re.sub(r'\bUNION\s+ALL\b', '', normalized)
        if re.search(r'\bUNION\b', stripped_union):
            return False

        if self._select_list_contains_scalar_subquery(normalized):
            return False

        return True

    def _select_list_contains_scalar_subquery(self, normalized_sql: str) -> bool:
        if not normalized_sql.startswith('SELECT '):
            return False

        depth = 0
        buf: List[str] = []
        i = len('SELECT ')
        while i < len(normalized_sql):
            ch = normalized_sql[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
            elif depth == 0 and normalized_sql.startswith(' FROM ', i):
                break
            buf.append(ch)
            i += 1
        return '(SELECT ' in ''.join(buf)

    def _main_table(self) -> Tuple[str, List[Any]]:
        for table_name, cols in self.tables:
            if table_name == self.main_table_name:
                return table_name, cols
        return self.tables[0]

    # ──────────────────────────────────────────
    # Miscellaneous helpers
    # ──────────────────────────────────────────

    def _hot(self, col: Any) -> Optional[str]:
        for _, col_map in self.hot_values.items():
            vals = col_map.get(col.name)
            if not vals:
                continue
            candidate = random.choice(vals)
            is_quoted = candidate.startswith("'")
            if col.data_type in _NUMERIC_FAMILY and is_quoted:
                return None
            if self._is_string_like_type(col.data_type) and not is_quoted and candidate != 'NULL':
                return None
            return candidate
        return None

    def _is_string_like_type(self, data_type: str) -> bool:
        return data_type in _STRING_FAMILY

    def _declared_type(self, col: Any) -> str:
        return getattr(col, 'declared_type', col.data_type or '').upper()

    def _is_enum_col(self, col: Any) -> bool:
        return col.data_type == 'ENUM' or self._declared_type(col).startswith('ENUM')

    def _is_set_col(self, col: Any) -> bool:
        return col.data_type == 'SET' or self._declared_type(col).startswith('SET(')

    def _is_char_col(self, col: Any) -> bool:
        return col.data_type == 'CHAR' or self._declared_type(col).startswith('CHAR')

    def _is_timestampish_col(self, col: Any) -> bool:
        dt = self._declared_type(col)
        return col.data_type in ('DATETIME', 'TIMESTAMP') or dt.startswith('DATETIME') or dt.startswith('TIMESTAMP')

    def _is_known_date_index_string_eq(self, c1: Any, c2: Any) -> bool:
        if not self._enable_known_mysql_date_index_string_eq_workaround:
            return False
        left_date = c1.data_type == 'DATE' and getattr(c1, 'is_indexed', False)
        right_date = c2.data_type == 'DATE' and getattr(c2, 'is_indexed', False)
        left_str = self._is_string_like_type(c1.data_type)
        right_str = self._is_string_like_type(c2.data_type)
        return (left_date and right_str) or (right_date and left_str)

    def _is_known_mysql_date_string_equality_risk(self, c1: Any, c2: Any) -> bool:
        if not self._enable_known_mysql_date_index_string_eq_workaround:
            return False
        left_date = c1.data_type == 'DATE'
        right_date = c2.data_type == 'DATE'
        left_str = self._is_string_like_type(c1.data_type)
        right_str = self._is_string_like_type(c2.data_type)
        return (left_date and right_str) or (right_date and left_str)

    def _is_known_mysql_year_string_in_subquery_risk(self, c1: Any, c2: Any) -> bool:
        if not self._enable_known_mysql_date_index_string_eq_workaround:
            return False
        left_year = c1.data_type == 'YEAR'
        right_year = c2.data_type == 'YEAR'
        left_str = self._is_string_like_type(c1.data_type)
        right_str = self._is_string_like_type(c2.data_type)
        return (left_year and right_str) or (right_year and left_str)

    def _is_known_mysql_exists_order_by_year_string_risk(self, c1: Any, c2: Any) -> bool:
        return (
            self._dialect.optimizer_family() == 'mysql'
            and self._is_known_mysql_year_string_in_subquery_risk(c1, c2)
        )

    def _null_test_signature(self, pred: str) -> Optional[Tuple[str, bool]]:
        m = re.fullmatch(
            r"\s*(`[^`]+`\.`[^`]+`)\s+IS\s+(NOT\s+)?NULL\s*",
            pred,
            re.IGNORECASE,
        )
        if not m:
            return None
        return (m.group(1), bool(m.group(2)))

    def _would_form_known_mysql_null_contradiction(self,
                                                   preds: List[str],
                                                   pred: str) -> bool:
        if not self._enable_known_mysql_date_index_string_eq_workaround:
            return False
        sig = self._null_test_signature(pred)
        if sig is None:
            return False
        ref, is_not_null = sig
        for existing in preds:
            existing_sig = self._null_test_signature(existing)
            if existing_sig and existing_sig[0] == ref and existing_sig[1] != is_not_null:
                return True
        return False

    def _is_risky_cross_type_pair(self, c1: Any, c2: Any) -> bool:
        if self._is_monetdb_family():
            return False
        if c1.data_type == 'OPAQUE' or c2.data_type == 'OPAQUE':
            return False
        if c1.data_type == c2.data_type:
            return False
        left_num = c1.data_type in _NUMERIC_FAMILY
        right_num = c2.data_type in _NUMERIC_FAMILY
        left_str = self._is_string_like_type(c1.data_type)
        right_str = self._is_string_like_type(c2.data_type)
        left_date = c1.data_type in _TEMPORAL_FAMILY
        right_date = c2.data_type in _TEMPORAL_FAMILY
        return (
            (left_date and right_str) or (right_date and left_str)
            or (left_num and right_str) or (right_num and left_str)
        )

    def _alias(self, base: str) -> str:
        self._ctr += 1
        safe = re.sub(r'[^a-z0-9]', '', base.lower())[:3] or 'tbl'
        return f"{safe}{self._ctr}"

    # ──────────────────────────────────────────
    # Dialect helpers
    # ──────────────────────────────────────────

    def _qi(self, name: str) -> str:
        """Quoted identifier."""
        return self._dialect.quote_identifier(name)

    def _qref(self, alias: str, col_name: str) -> str:
        """Quoted alias.column reference."""
        if not alias:
            return self._qi(col_name)
        return self._dialect.quote_qualified_name(alias, col_name)

    def _fn(self, name: str) -> str:
        """Dialect-specific function name."""
        return self._dialect.get_function_name(name)

    def _lit(self, value: str, data_type: str) -> str:
        """Dialect-specific literal representation."""
        return self._dialect.get_literal_representation(value, data_type)
