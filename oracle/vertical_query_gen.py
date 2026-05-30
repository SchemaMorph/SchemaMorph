"""
oracle/vertical_query_gen.py

Vertical-specific query generator built on top of SubsetQueryGenerator.

Design goal:
- keep the row-preserving / monotone semantics required by VerticalOracle
- bias generation toward relaxed-column behavior
- fold former forced-query ideas into probabilistic generator shapes
- still reuse the richer projection / derived / CTE / join machinery from
  SubsetQueryGenerator for complexity and diversity
"""

import random
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

from oracle.subset_query_gen import (
    SubsetQueryGenerator,
    _MAX_GENERATE_RETRIES,
    _NUMERIC_FAMILY,
    _STRING_FAMILY,
    _TEMPORAL_FAMILY,
)


_VERTICAL_SPECIAL_QUERY_PROB = 0.58


class VerticalQueryGenerator(SubsetQueryGenerator):
    def __init__(
        self,
        tables: List[Tuple[str, List[Any]]],
        skew_hot_values: Optional[Dict[str, Dict[str, List[str]]]] = None,
        dialect=None,
        enable_known_mysql_date_index_string_eq_workaround: Optional[bool] = None,
        main_table_name: Optional[str] = None,
        relaxed_cols: Optional[Set[str]] = None,
        null_relaxed_cols: Optional[Set[str]] = None,
        unique_cols: Optional[Set[str]] = None,
        check_cols: Optional[Set[str]] = None,
        fk_relaxed_map: Optional[Dict[str, Tuple[str, str]]] = None,
        special_query_prob: float = _VERTICAL_SPECIAL_QUERY_PROB,
        ablation_config=None,
    ) -> None:
        super().__init__(
            tables=tables,
            skew_hot_values=skew_hot_values,
            dialect=dialect,
            enable_known_mysql_date_index_string_eq_workaround=(
                enable_known_mysql_date_index_string_eq_workaround
            ),
            ablation_config=ablation_config,
        )
        self.main_table_name = main_table_name or tables[0][0]
        self.main_cols = next(cols for name, cols in tables if name == self.main_table_name)
        self.aux_tables = [(name, cols) for name, cols in tables if name != self.main_table_name]
        self.relaxed_cols = set(relaxed_cols or set())
        self.null_relaxed_cols = set(null_relaxed_cols or set())
        self.unique_cols = set(unique_cols or set())
        self.check_cols = set(check_cols or set())
        self.fk_relaxed_map = dict(fk_relaxed_map or {})
        self.special_query_prob = (
            special_query_prob
            if ablation_config is None or ablation_config.opt5_constraint_bias
            else 0.0
        )
        self.last_tag = 'generator'

    def generate(self) -> Optional[str]:
        builders = self._shape_builders()
        for _ in range(_MAX_GENERATE_RETRIES):
            self._ctr = 0
            shape = None
            try:
                shape = self._choose_shape()
                builder = builders.get(shape)
                if builder is None:
                    continue
                sql = builder()
                if sql and self._validate_monotone_sql(sql):
                    self.last_tag = self._shape_tag(shape)
                    return sql
            except Exception as e:
                print(f"[vertical gen ERROR] shape={shape}: {e}")
                traceback.print_exc()
        return None

    def generate_tagged(self) -> Tuple[str, Optional[str]]:
        sql = self.generate()
        return self.last_tag, sql

    def _choose_shape(self) -> str:
        pool = self._base_shape_pool()
        vertical_pool = self._vertical_shape_pool()
        if vertical_pool:
            base_total = sum(weight for _, weight in pool)
            desired_vertical_total = max(
                1,
                int(base_total * self.special_query_prob / max(0.01, 1.0 - self.special_query_prob)),
            )
            raw_vertical_total = sum(weight for _, weight in vertical_pool)
            scaled_vertical: List[Tuple[str, int]] = []
            for shape, weight in vertical_pool:
                scaled = max(1, int(round(weight * desired_vertical_total / max(1, raw_vertical_total))))
                scaled_vertical.append((shape, scaled))
            pool.extend(scaled_vertical)
        shapes, weights = zip(*pool)
        return random.choices(shapes, weights=weights, k=1)[0]

    def _shape_builders(self) -> Dict[str, Any]:
        return {
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
            'VERTICAL_CROSS_TYPE_JOIN': self._build_vertical_cross_type_join,
            'VERTICAL_CROSS_TYPE_EXISTS': self._build_vertical_cross_type_exists,
            'VERTICAL_CROSS_TYPE_DERIVED': self._build_vertical_cross_type_derived,
            'VERTICAL_CROSS_TYPE_CORRELATED': self._build_vertical_cross_type_correlated,
            'VERTICAL_CHECK_RANGE': self._build_vertical_check_range,
            'VERTICAL_CHECK_CTE': self._build_vertical_check_cte,
            'VERTICAL_CHECK_CROSS_TYPE': self._build_vertical_check_cross_type,
            'VERTICAL_FK_EXISTS': self._build_vertical_fk_exists,
            'VERTICAL_FK_JOIN': self._build_vertical_fk_join,
            'VERTICAL_FK_DERIVED': self._build_vertical_fk_derived,
            'VERTICAL_UNIQUE_SEMIJOIN': self._build_vertical_unique_semijoin,
            'VERTICAL_UNIQUE_CTE': self._build_vertical_unique_cte,
            'VERTICAL_NULL_DERIVED': self._build_vertical_null_derived,
            'VERTICAL_RELAXED_MIXED': self._build_vertical_relaxed_mixed_query,
            'VERTICAL_RELAXED_CTE': self._build_vertical_relaxed_cte,
        }

    def _shape_tag(self, shape: str) -> str:
        if shape.startswith('VERTICAL_CROSS_TYPE'):
            return 'cross_type'
        if shape.startswith('VERTICAL_CHECK'):
            return 'check'
        if shape.startswith('VERTICAL_FK'):
            return 'fk'
        if shape.startswith('VERTICAL_UNIQUE'):
            return 'unique'
        if shape.startswith('VERTICAL_NULL'):
            return 'null'
        if shape.startswith('VERTICAL_'):
            return 'mixed'
        return 'generator'

    def _base_shape_pool(self) -> List[Tuple[str, int]]:
        n = len(self.tables)
        cte_w = 10 if self._dialect.supports_cte() else 0
        if n == 1:
            return [
                ('SINGLE', 34),
                ('SELF_JOIN', 18),
                ('CTE_WRAPPER', cte_w),
                ('DERIVED_TABLE', 22),
                ('IN_SUBQUERY', 10),
                ('EXISTS_SUBQUERY', 6),
                ('NESTED_DERIVED', 4),
                ('UNION_ALL', 2),
            ]
        if n == 2:
            return [
                ('SINGLE', 8),
                ('INNER_JOIN_2', 24),
                ('IMPLICIT_CONVERSION_JOIN', 16),
                ('RARE_BEHAVIOR_JOIN', 18),
                ('SELF_JOIN', 10),
                ('CROSS_JOIN_FILTERED', 10),
                ('CTE_WRAPPER', cte_w),
                ('DERIVED_TABLE', 14),
                ('IN_SUBQUERY', 14),
                ('EXISTS_SUBQUERY', 10),
                ('NESTED_DERIVED', 4),
                ('UNION_ALL', 2),
            ]
        return [
            ('SINGLE', 6),
            ('INNER_JOIN_2', 20),
            ('IMPLICIT_CONVERSION_JOIN', 14),
            ('RARE_BEHAVIOR_JOIN', 16),
            ('INNER_JOIN_3', 14),
            ('SELF_JOIN', 10),
            ('CROSS_JOIN_FILTERED', 8),
            ('CTE_WRAPPER', cte_w),
            ('DERIVED_TABLE', 14),
            ('IN_SUBQUERY', 12),
            ('EXISTS_SUBQUERY', 8),
            ('NESTED_DERIVED', 4),
            ('UNION_ALL', 2),
        ]

    def _vertical_shape_pool(self) -> List[Tuple[str, int]]:
        pool: List[Tuple[str, int]] = []
        if self._cross_type_candidates():
            pool.extend([
                ('VERTICAL_CROSS_TYPE_JOIN', 30),
                ('VERTICAL_CROSS_TYPE_EXISTS', 18),
                ('VERTICAL_CROSS_TYPE_DERIVED', 14),
                ('VERTICAL_CROSS_TYPE_CORRELATED', 12),
            ])
        if self.check_cols:
            pool.extend([
                ('VERTICAL_CHECK_RANGE', 22),
                ('VERTICAL_CHECK_CTE', 12),
                ('VERTICAL_CHECK_CROSS_TYPE', 12),
            ])
        if self.fk_relaxed_map:
            pool.extend([
                ('VERTICAL_FK_EXISTS', 18),
                ('VERTICAL_FK_JOIN', 14),
                ('VERTICAL_FK_DERIVED', 10),
            ])
        if self.unique_cols:
            pool.extend([
                ('VERTICAL_UNIQUE_SEMIJOIN', 14),
                ('VERTICAL_UNIQUE_CTE', 10),
            ])
        if self.null_relaxed_cols:
            pool.extend([
                ('VERTICAL_NULL_DERIVED', 12),
            ])
        if self.relaxed_cols:
            pool.extend([
                ('VERTICAL_RELAXED_MIXED', 18),
                ('VERTICAL_RELAXED_CTE', 12),
            ])
        return pool

    def _relaxed_main_cols(self) -> List[Any]:
        cols = [c for c in self.main_cols if c.name in self.relaxed_cols and not c.is_primary_key]
        random.shuffle(cols)
        return cols

    def _cross_type_candidates(self) -> List[Tuple[Any, str, Any]]:
        pairs: List[Tuple[Any, str, Any]] = []
        for main_col in self._relaxed_main_cols():
            if not (
                main_col.data_type in _TEMPORAL_FAMILY
                or main_col.data_type in _NUMERIC_FAMILY
                or main_col.data_type in _STRING_FAMILY
            ):
                continue
            for aux_name, aux_cols in self.aux_tables:
                for aux_col in aux_cols:
                    if aux_col.is_primary_key:
                        continue
                    if aux_col.data_type not in _STRING_FAMILY:
                        continue
                    if self._same_comparable_type(
                        main_col,
                        aux_col,
                        allow_known_date_index_string_eq=False,
                        allow_mysql_date_string_equality=False,
                    ):
                        pairs.append((main_col, aux_name, aux_col))
        random.shuffle(pairs)
        return pairs

    def _hot_literals_for_main_col(self, col: Any) -> List[str]:
        vals = self.hot_values.get(self.main_table_name, {}).get(col.name, [])
        out: List[str] = []
        for val in vals[:3]:
            if val == 'NULL':
                continue
            out.append(val)
        if out:
            return out
        if col.data_type == 'DATE':
            return ["'2023-01-01'", "'0000-00-00'", "'not-a-date'"]
        if col.data_type in ('DATETIME', 'TIMESTAMP'):
            return ["'2023-01-01 00:00:00'", "'2023-01-01'", "'not-a-date'"]
        if col.data_type == 'YEAR':
            return ["'2023'", "'1999'", "'023'"]
        if col.data_type in _NUMERIC_FAMILY:
            return ["'0'", "'1'", "'01e0'"]
        if col.data_type in _STRING_FAMILY:
            return ["'01e0'", "'not-a-date'", "'2023-01-01'"]
        return []

    def _build_vertical_cross_type_join(self) -> Optional[str]:
        pairs = self._cross_type_candidates()
        if not pairs:
            return None
        main_col, aux_name, aux_col = pairs[0]
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(aux_name)
        alias_cols = [(a1, self.main_cols), (a2, self._cols_for_table(aux_name))]
        on = f"{self._qref(a1, main_col.name)} = {self._qref(a2, aux_col.name)}"
        where = self._where_clause(alias_cols, max_preds=2)
        hot_vals = self._hot_literals_for_main_col(main_col)
        if hot_vals and random.random() < 0.75:
            where = self._merge_where(
                where,
                f"{self._qref(a2, aux_col.name)} IN ({', '.join(hot_vals[:min(3, len(hot_vals))])})",
            )
        sql = (
            f"SELECT /*vertical_cross_type*/ {self._select_list(alias_cols)} "
            f"FROM {self._qi(self.main_table_name)} {a1} "
            f"INNER JOIN {self._qi(aux_name)} {a2} ON {on}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_cross_type_exists(self) -> Optional[str]:
        pairs = self._cross_type_candidates()
        if not pairs:
            return None
        main_col, aux_name, aux_col = pairs[0]
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(aux_name)
        alias_cols = [(a1, self.main_cols)]
        hot_vals = self._hot_literals_for_main_col(main_col)
        corr = f"{self._qref(a1, main_col.name)} = {self._qref(a2, aux_col.name)}"
        sub_where = self._merge_where('', corr)
        if hot_vals and random.random() < 0.8:
            sub_where = self._merge_where(
                sub_where,
                f"{self._qref(a2, aux_col.name)} IN ({', '.join(hot_vals[:min(3, len(hot_vals))])})",
            )
        exists_pred = f"EXISTS (SELECT 1 FROM {self._qi(aux_name)} {a2}{sub_where})"
        where = self._merge_where(self._where_clause(alias_cols, max_preds=2), exists_pred)
        sql = f"SELECT {self._select_list(alias_cols)} FROM {self._qi(self.main_table_name)} {a1}{where}"
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_cross_type_derived(self) -> Optional[str]:
        pairs = self._cross_type_candidates()
        if not pairs:
            return None
        main_col, aux_name, aux_col = pairs[0]
        inner_a = self._alias('i')
        chosen = self._pick_projection_templates([(inner_a, self.main_cols)], 2, 4)
        keep_cols = []
        seen_names = set()
        for _, col in chosen:
            if col.name not in seen_names:
                keep_cols.append(col)
                seen_names.add(col.name)
        if main_col.name not in seen_names:
            keep_cols.append(main_col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        inner_where = self._where_clause([(inner_a, self.main_cols)], max_preds=2)
        hot_vals = self._hot_literals_for_main_col(main_col)
        if hot_vals and random.random() < 0.8:
            inner_where = self._merge_where(
                inner_where,
                f"{self._qref(inner_a, main_col.name)} IS NOT NULL",
            )
        inner_sql = f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a}{inner_where}"

        sub_a = self._alias('s')
        aux_a = self._alias(aux_name)
        alias_cols = [(sub_a, keep_cols), (aux_a, self._cols_for_table(aux_name))]
        on = f"{self._qref(sub_a, main_col.name)} = {self._qref(aux_a, aux_col.name)}"
        where = self._where_clause(alias_cols, max_preds=2)
        if hot_vals and random.random() < 0.7:
            where = self._merge_where(
                where,
                f"{self._qref(aux_a, aux_col.name)} IN ({', '.join(hot_vals[:min(3, len(hot_vals))])})",
            )
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM ({inner_sql}) {sub_a} "
            f"INNER JOIN {self._qi(aux_name)} {aux_a} ON {on}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_cross_type_correlated(self) -> Optional[str]:
        pairs = self._cross_type_candidates()
        if not pairs:
            return None
        main_col, aux_name, aux_col = pairs[0]
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(aux_name)
        alias_cols = [(a1, self.main_cols)]
        hot_vals = self._hot_literals_for_main_col(main_col)
        sub_where = self._merge_where(
            self._where_clause([(a2, self._cols_for_table(aux_name))], max_preds=1),
            f"{self._qref(a1, main_col.name)} = {self._qref(a2, aux_col.name)}",
        )
        if hot_vals and random.random() < 0.8:
            sub_where = self._merge_where(
                sub_where,
                f"{self._qref(a2, aux_col.name)} IN ({', '.join(hot_vals[:min(3, len(hot_vals))])})",
            )
        exists_pred = f"EXISTS (SELECT 1 FROM {self._qi(aux_name)} {a2}{sub_where})"
        base_where = self._merge_where(self._where_clause(alias_cols, max_preds=2), exists_pred)
        relaxed_pred = self._relaxed_predicate(a1, main_col)
        where = self._merge_where(base_where, relaxed_pred)
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM {self._qi(self.main_table_name)} {a1}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_check_range(self) -> Optional[str]:
        if not self.check_cols:
            return None
        col = self._col_by_name(random.choice(list(self.check_cols)))
        if col is None:
            return None
        a1 = self._alias(self.main_table_name)
        alias_cols = [(a1, self.main_cols)]
        base_where = self._where_clause(alias_cols, max_preds=2)
        range_pred = f"{self._qref(a1, col.name)} BETWEEN 1 AND 100"
        hot_vals = [v for v in self._hot_literals_for_main_col(col) if not v.startswith("'")]
        if hot_vals and random.random() < 0.5:
            pred = f"{self._qref(a1, col.name)} IN ({', '.join(hot_vals[:min(2, len(hot_vals))])})"
        else:
            pred = range_pred
        where = self._merge_where(base_where, pred)
        sql = f"SELECT {self._select_list(alias_cols)} FROM {self._qi(self.main_table_name)} {a1}{where}"
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_check_cte(self) -> Optional[str]:
        if not self.check_cols or not self._dialect.supports_cte():
            return None
        col = self._col_by_name(random.choice(list(self.check_cols)))
        if col is None:
            return None
        inner_a = self._alias('c')
        keep_cols = random.sample(self.main_cols, k=min(max(2, len(self.main_cols) // 2), len(self.main_cols)))
        if col not in keep_cols:
            keep_cols.append(col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        inner_sql = (
            f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a} "
            f"WHERE {self._qref(inner_a, col.name)} > 0"
        )
        cte_name = f"vcte_{random.randint(1, 999)}"
        out_a = self._alias('vcte')
        out_cols = [(out_a, keep_cols)]
        where = self._merge_where(
            self._where_clause(out_cols, max_preds=2),
            f"{self._qref(out_a, col.name)} BETWEEN 1 AND 100",
        )
        sql = (
            f"WITH {cte_name} AS ({inner_sql}) "
            f"SELECT {self._select_list(out_cols)} FROM {cte_name} {out_a}{where}"
        )
        return self._maybe_add_order_by(sql, out_cols)

    def _build_vertical_check_cross_type(self) -> Optional[str]:
        if not self.check_cols:
            return None
        pairs = self._cross_type_candidates()
        if not pairs:
            return self._build_vertical_check_range()
        check_col = self._col_by_name(random.choice(list(self.check_cols)))
        if check_col is None:
            return None
        main_col, aux_name, aux_col = random.choice(pairs)
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(aux_name)
        alias_cols = [(a1, self.main_cols), (a2, self._cols_for_table(aux_name))]
        on = f"{self._qref(a1, main_col.name)} = {self._qref(a2, aux_col.name)}"
        hot_vals = self._hot_literals_for_main_col(main_col)
        where = self._where_clause(alias_cols, max_preds=2)
        where = self._merge_where(where, f"{self._qref(a1, check_col.name)} BETWEEN 1 AND 100")
        if hot_vals and random.random() < 0.75:
            where = self._merge_where(
                where,
                f"{self._qref(a2, aux_col.name)} IN ({', '.join(hot_vals[:min(3, len(hot_vals))])})",
            )
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM {self._qi(self.main_table_name)} {a1} "
            f"INNER JOIN {self._qi(aux_name)} {a2} ON {on}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_fk_exists(self) -> Optional[str]:
        if not self.fk_relaxed_map:
            return None
        main_col_name, (ref_table, ref_col_name) = random.choice(list(self.fk_relaxed_map.items()))
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(ref_table)
        alias_cols = [(a1, self.main_cols)]
        corr = f"{self._qref(a1, main_col_name)} = {self._qref(a2, ref_col_name)}"
        exists_pred = f"EXISTS (SELECT 1 FROM {self._qi(ref_table)} {a2} WHERE {corr})"
        where = self._merge_where(self._where_clause(alias_cols, max_preds=2), exists_pred)
        sql = f"SELECT {self._select_list(alias_cols)} FROM {self._qi(self.main_table_name)} {a1}{where}"
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_fk_join(self) -> Optional[str]:
        if not self.fk_relaxed_map:
            return None
        main_col_name, (ref_table, ref_col_name) = random.choice(list(self.fk_relaxed_map.items()))
        a1 = self._alias(self.main_table_name)
        a2 = self._alias(ref_table)
        alias_cols = [(a1, self.main_cols), (a2, self._cols_for_table(ref_table))]
        on = f"{self._qref(a1, main_col_name)} = {self._qref(a2, ref_col_name)}"
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM {self._qi(self.main_table_name)} {a1} "
            f"INNER JOIN {self._qi(ref_table)} {a2} ON {on}"
            f"{self._where_clause(alias_cols, max_preds=2)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_fk_derived(self) -> Optional[str]:
        if not self.fk_relaxed_map:
            return None
        main_col_name, (ref_table, ref_col_name) = random.choice(list(self.fk_relaxed_map.items()))
        inner_a = self._alias('i')
        keep_cols = random.sample(self.main_cols, k=min(4, len(self.main_cols)))
        if not any(c.name == main_col_name for c in keep_cols):
            main_col = self._col_by_name(main_col_name)
            if main_col is not None:
                keep_cols.append(main_col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        inner_sql = (
            f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a}"
            f"{self._where_clause([(inner_a, self.main_cols)], max_preds=2)}"
        )
        sub_a = self._alias('d')
        ref_a = self._alias(ref_table)
        alias_cols = [(sub_a, keep_cols), (ref_a, self._cols_for_table(ref_table))]
        on = f"{self._qref(sub_a, main_col_name)} = {self._qref(ref_a, ref_col_name)}"
        sql = (
            f"SELECT {self._select_list(alias_cols)} "
            f"FROM ({inner_sql}) {sub_a} "
            f"INNER JOIN {self._qi(ref_table)} {ref_a} ON {on}"
            f"{self._where_clause(alias_cols, max_preds=2)}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_unique_semijoin(self) -> Optional[str]:
        candidates = [self._col_by_name(name) for name in self.unique_cols]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return None
        col = random.choice(candidates)
        a1 = self._alias(self.main_table_name)
        a2 = self._alias('sq')
        alias_cols = [(a1, self.main_cols)]
        hot_vals = self._hot_literals_for_main_col(col)
        sub_where = f"{self._qref(a2, col.name)} IS NOT NULL"
        if hot_vals and random.random() < 0.75:
            sub_where = self._merge_where(
                f" WHERE {sub_where}",
                f"{self._qref(a2, col.name)} IN ({', '.join(hot_vals[:min(2, len(hot_vals))])})",
            )
        else:
            sub_where = f" WHERE {sub_where}"
        subquery = f"SELECT {self._qref(a2, col.name)} FROM {self._qi(self.main_table_name)} {a2}{sub_where}"
        where = self._merge_where(
            self._where_clause(alias_cols, max_preds=2),
            f"{self._qref(a1, col.name)} IN ({subquery})",
        )
        sql = f"SELECT {self._select_list(alias_cols)} FROM {self._qi(self.main_table_name)} {a1}{where}"
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_unique_cte(self) -> Optional[str]:
        if not self.unique_cols or not self._dialect.supports_cte():
            return None
        col = self._col_by_name(random.choice(list(self.unique_cols)))
        if col is None:
            return None
        inner_a = self._alias('u')
        hot_vals = self._hot_literals_for_main_col(col)
        inner_where = self._merge_where(
            self._where_clause([(inner_a, self.main_cols)], max_preds=2),
            f"{self._qref(inner_a, col.name)} IS NOT NULL",
        )
        if hot_vals and random.random() < 0.7:
            inner_where = self._merge_where(
                inner_where,
                f"{self._qref(inner_a, col.name)} IN ({', '.join(hot_vals[:min(2, len(hot_vals))])})",
            )
        keep_cols = random.sample(self.main_cols, k=min(4, len(self.main_cols)))
        if col not in keep_cols:
            keep_cols.append(col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        cte_name = f"vuniq_{random.randint(1, 999)}"
        inner_sql = f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a}{inner_where}"
        out_a = self._alias('uq')
        out_cols = [(out_a, keep_cols)]
        where = self._merge_where(
            self._where_clause(out_cols, max_preds=2),
            f"{self._qref(out_a, col.name)} IN (SELECT {self._qref(out_a, col.name)} FROM {cte_name} {out_a})",
        )
        sql = f"WITH {cte_name} AS ({inner_sql}) SELECT {self._select_list(out_cols)} FROM {cte_name} {out_a}{where}"
        return self._maybe_add_order_by(sql, out_cols)

    def _build_vertical_null_derived(self) -> Optional[str]:
        candidates = [self._col_by_name(name) for name in self.null_relaxed_cols]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return None
        col = random.choice(candidates)
        inner_a = self._alias('i')
        keep_cols = random.sample(self.main_cols, k=min(4, len(self.main_cols)))
        if col not in keep_cols:
            keep_cols.append(col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        inner_where = self._merge_where(
            self._where_clause([(inner_a, self.main_cols)], max_preds=2),
            f"({self._qref(inner_a, col.name)} IS NULL OR {self._qref(inner_a, col.name)} IS NOT NULL)",
        )
        inner_sql = f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a}{inner_where}"
        out_a = self._alias('d')
        out_cols = [(out_a, keep_cols)]
        hot_vals = self._hot_literals_for_main_col(col)
        pred = f"{self._qref(out_a, col.name)} IS NULL"
        if hot_vals and random.random() < 0.65:
            pred = (
                f"({self._qref(out_a, col.name)} IS NULL OR "
                f"{self._qref(out_a, col.name)} IN ({', '.join(hot_vals[:min(2, len(hot_vals))])}))"
            )
        where = self._merge_where(self._where_clause(out_cols, max_preds=2), pred)
        sql = f"SELECT {self._select_list(out_cols)} FROM ({inner_sql}) {out_a}{where}"
        return self._maybe_add_order_by(sql, out_cols)

    def _build_vertical_relaxed_mixed_query(self) -> Optional[str]:
        relaxed = self._relaxed_main_cols()
        if not relaxed:
            return None
        main_col = relaxed[0]
        a1 = self._alias(self.main_table_name)
        alias_cols = [(a1, self.main_cols)]
        outer_templates = self._pick_projection_templates(alias_cols, 2, 4)
        base_where = self._where_clause(alias_cols, max_preds=2)
        pred = self._relaxed_predicate(a1, main_col)
        where = self._merge_where(base_where, pred)
        if self.aux_tables and random.random() < 0.55:
            aux_name, aux_cols = random.choice(self.aux_tables)
            a2 = self._alias(aux_name)
            pair = self._pick_compat_col(
                aux_cols,
                main_col,
                allow_known_date_index_string_eq=False,
                allow_mysql_date_string_equality=False,
            )
            if pair is not None:
                exists_pred = (
                    f"EXISTS (SELECT 1 FROM {self._qi(aux_name)} {a2} "
                    f"WHERE {self._qref(a1, main_col.name)} = {self._qref(a2, pair.name)})"
                )
                where = self._merge_where(where, exists_pred)
        sql = (
            f"SELECT {self._select_list_from_templates(alias_cols, outer_templates)} "
            f"FROM {self._qi(self.main_table_name)} {a1}{where}"
        )
        return self._maybe_add_order_by(sql, alias_cols)

    def _build_vertical_relaxed_cte(self) -> Optional[str]:
        if not self.relaxed_cols or not self._dialect.supports_cte():
            return None
        main_col = random.choice(self._relaxed_main_cols())
        inner_a = self._alias('r')
        keep_cols = random.sample(self.main_cols, k=min(max(3, len(self.main_cols) // 2), len(self.main_cols)))
        if main_col not in keep_cols:
            keep_cols.append(main_col)
        inner_sel = ', '.join(
            f"{self._qref(inner_a, c.name)} AS {self._qi(c.name)}" for c in keep_cols
        )
        inner_where = self._merge_where(
            self._where_clause([(inner_a, self.main_cols)], max_preds=2),
            self._relaxed_predicate(inner_a, main_col),
        )
        if self.aux_tables and random.random() < 0.6:
            aux_name, aux_cols = random.choice(self.aux_tables)
            aux_a = self._alias(aux_name)
            pair = self._pick_compat_col(
                aux_cols,
                main_col,
                allow_known_date_index_string_eq=False,
                allow_mysql_date_string_equality=False,
            )
            if pair is not None:
                inner_where = self._merge_where(
                    inner_where,
                    f"EXISTS (SELECT 1 FROM {self._qi(aux_name)} {aux_a} "
                    f"WHERE {self._qref(inner_a, main_col.name)} = {self._qref(aux_a, pair.name)})",
                )
        cte_name = f"vrel_{random.randint(1, 999)}"
        inner_sql = f"SELECT {inner_sel} FROM {self._qi(self.main_table_name)} {inner_a}{inner_where}"
        out_a = self._alias('rv')
        out_cols = [(out_a, keep_cols)]
        where = self._where_clause(out_cols, max_preds=2)
        sql = f"WITH {cte_name} AS ({inner_sql}) SELECT {self._select_list(out_cols)} FROM {cte_name} {out_a}{where}"
        return self._maybe_add_order_by(sql, out_cols)

    def _relaxed_predicate(self, alias: str, col: Any) -> str:
        ref = self._qref(alias, col.name)
        hot_vals = self._hot_literals_for_main_col(col)
        if col.name in self.check_cols and col.data_type in _NUMERIC_FAMILY:
            return f"{ref} BETWEEN 1 AND 100"
        if hot_vals and random.random() < 0.7:
            return f"{ref} IN ({', '.join(hot_vals[:min(2, len(hot_vals))])})"
        if col.name in self.null_relaxed_cols:
            return f"({ref} IS NULL OR {ref} IS NOT NULL)"
        return f"{ref} IS NOT NULL"

    def _col_by_name(self, name: str) -> Optional[Any]:
        return next((c for c in self.main_cols if c.name == name), None)

    def _cols_for_table(self, table_name: str) -> List[Any]:
        for name, cols in self.tables:
            if name == table_name:
                return cols
        return []
