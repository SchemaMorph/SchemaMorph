from typing import List, Tuple, Dict, Optional, Set
import random
from .ast_node import ASTNode
from .from_node import FromNode
from .group_by_node import GroupByNode
from .order_by_node import OrderByNode
from .limit_node import LimitNode
from .function_call_node import FunctionCallNode
from .column_reference_node import ColumnReferenceNode
from .comparison_node import ComparisonNode
from .literal_node import LiteralNode
from .subquery_node import SubqueryNode
from data_structures.db_dialect import get_current_dialect
from data_structures.node_type import NodeType
from data_structures.table import Table
from data_structures.function import Function
# Import ColumnUsageTracker for tracking column usage
from generate_random_sql import ColumnUsageTracker, get_random_column_with_tracker

class SelectNode(ASTNode):
    """SELECT statement node"""

    def __init__(self):
        super().__init__(NodeType.SELECT)
        self.distinct = False
        self.select_expressions: List[Tuple[ASTNode, str]] = []  # (expression, alias)
        self.from_clause: Optional[FromNode] = None
        self.where_clause: Optional[ASTNode] = None
        self.group_by_clause: Optional[GroupByNode] = None
        self.having_clause: Optional[ASTNode] = None
        self.order_by_clause: Optional[OrderByNode] = None
        self.limit_clause: Optional[LimitNode] = None
        self.for_update: Optional[str] = None  # Support multiple locking modes: 'update', 'share', 'no key update', 'key share'
        self.tables: List[Table] = []  # Associated table information
        self.functions: List[Function] = []  # Available functions
        self.column_tracker: Optional[ColumnUsageTracker] = None  # Column usage tracker

    def add_select_expression(self, expr_node: ASTNode, alias: str = "") -> None:
        # Check if alias is duplicate
        used_aliases = self.get_select_column_aliases()
        base_alias = alias if alias else f"col_{len(self.select_expressions) + 1}"
        final_alias = base_alias
        count = 1
        
        # Handle duplicate aliases
        while final_alias in used_aliases:
            final_alias = f"{base_alias}_{count}"
            count += 1
        self.select_expressions.append((expr_node, final_alias))
        self.add_child(expr_node)

    def get_expression_alias_map(self) -> Dict[str, ASTNode]:
        """Create mapping from aliases to expressions"""
        return {alias: expr for expr, alias in self.select_expressions if alias}

    def get_alias_for_expression(self, expr_node: ASTNode) -> Optional[str]:
        """Get the alias for a given expression"""
        for expr, alias in self.select_expressions:
            if expr == expr_node:
                return alias
        return None

    def get_select_column_aliases(self) -> Set[str]:
        """Get all column aliases defined in the SELECT clause"""
        aliases = set()
        for i, (_, alias) in enumerate(self.select_expressions):
            # Include auto-generated default aliases
            aliases.add(alias if alias else f"col_{i + 1}")
        return aliases

    def set_from_clause(self, from_node: FromNode) -> None:
        self.from_clause = from_node
        if from_node:
            self.add_child(from_node)
            # Validate table references in FROM clause
            if hasattr(from_node, 'validate_table_references'):
                valid, errors = from_node.validate_table_references()
                if not valid and hasattr(from_node, 'repair_table_references'):
                    from_node.repair_table_references()

    def set_where_clause(self, where_node: ASTNode) -> None:
        # Key check 1: Ensure WHERE clause does not contain window functions
        if where_node.contains_window_function():
            # Create a simple WHERE condition alternative
            table_alias_map = self.from_clause.get_table_alias_map() if (
                    self.from_clause and hasattr(self.from_clause, 'get_table_alias_map')) else {}
            if table_alias_map:
                table_name = random.choice(list(table_alias_map.keys())) if table_alias_map else None
                if table_name:
                    table = next((t for t in self.tables if t.name == table_name), None) if hasattr(self,
                                                                                                    'tables') else None
                    if table:
                        col_node = ColumnReferenceNode(random.choice(table.columns), table_alias_map[table_name])
                        where_node = ComparisonNode('IS NOT NULL')
                        where_node.add_child(col_node)

        # Key check 2: Ensure WHERE clause does not contain aggregate functions (SQL syntax rule)
        if where_node.contains_aggregate_function():
            # Create a simple WHERE condition alternative (without aggregate functions)
            table_alias_map = self.from_clause.get_table_alias_map() if (
                    self.from_clause and hasattr(self.from_clause, 'get_table_alias_map')) else {}
            if table_alias_map:
                table_name = random.choice(list(table_alias_map.keys())) if table_alias_map else None
                if table_name:
                    table = next((t for t in self.tables if t.name == table_name), None) if hasattr(self,
                                                                                                    'tables') else None
                    if table:
                        col_node = ColumnReferenceNode(random.choice(table.columns), table_alias_map[table_name])
                        # Use simple comparison operator and literal
                        operator = random.choice(['=', '<>', '<', '>', '<=', '>=', 'IS NOT NULL'])
                        where_node = ComparisonNode(operator)
                        where_node.add_child(col_node)
                        
                        # Add right operand if needed
                        if operator not in ['IS NULL', 'IS NOT NULL']:
                            if col_node.column.category == 'numeric':
                                where_node.add_child(LiteralNode(random.randint(0, 100), col_node.column.data_type))
                            elif col_node.column.category == 'string':
                                where_node.add_child(LiteralNode(f"sample_{random.randint(1, 100)}", 'STRING'))
                            elif col_node.column.category == 'datetime':
                                where_node.add_child(LiteralNode('2023-01-01', col_node.column.data_type))

        # Key check 3: Ensure WHERE clause returns boolean type
        # All necessary classes have been imported at the top of the file, no need for local imports
        
        # If WHERE clause is a function call, check if return type is boolean
        if isinstance(where_node, FunctionCallNode):
            return_type = where_node.metadata.get('return_type', 'unknown')
            if return_type.lower() != 'boolean' and return_type.lower() != 'bool':
                # Return type is not boolean, need to wrap in comparison expression
                operator = random.choice(['=', '<>', '!='])
                new_where_node = ComparisonNode(operator)
                new_where_node.add_child(where_node)
                
                # Create appropriate literal based on return type
                # Ideally, should use global table structure information and Column object's category property to determine type
                # But since this method directly processes return type strings, temporarily retain keyword-based judgment
                if return_type.lower() in ['string', 'text', 'varchar', 'char']:
                    new_where_node.add_child(LiteralNode(f"sample_value", 'STRING'))
                elif return_type.lower() in ['numeric', 'int', 'integer', 'float', 'decimal']:
                    new_where_node.add_child(LiteralNode(random.randint(0, 100), 'INT'))
                elif return_type.lower() in ['date', 'datetime', 'timestamp']:
                    new_where_node.add_child(LiteralNode('2023-01-01', 'DATE'))
                else:
                    # Default to string type
                    new_where_node.add_child(LiteralNode(f"sample_value", 'STRING'))
                
                where_node = new_where_node

        self.where_clause = where_node
        if where_node:
            self.add_child(where_node)

    def set_group_by_clause(self, group_by_node: GroupByNode) -> None:
        self.group_by_clause = group_by_node
        if group_by_node:
            self.add_child(group_by_node)

    def set_having_clause(self, having_node: ASTNode) -> None:
        # Key check: Ensure HAVING clause does not contain window functions
        if having_node and having_node.contains_window_function():
            # Create a simple HAVING condition alternative (using aggregate function)
            count_func = next(f for f in self.functions if f.name == "COUNT")
            func_node = FunctionCallNode(count_func)
            col_ref = self._get_random_column_reference()
            if col_ref:
                func_node.add_child(col_ref)
                having_node = ComparisonNode('>')
                having_node.add_child(func_node)
                having_node.add_child(LiteralNode(0, "INT"))
            else:
                # If column reference cannot be obtained, use simple literal condition
                having_node = ComparisonNode('>')
                having_node.add_child(LiteralNode(0, "INT"))
                having_node.add_child(LiteralNode(0, "INT"))

        # Validate the validity of HAVING clause
        if not self._is_valid_having_clause(having_node):
            # Create a valid HAVING condition
            if self.group_by_clause and hasattr(self.group_by_clause,
                                                'expressions') and self.group_by_clause.expressions:
                # Create condition using expression from GROUP BY
                expr = random.choice(self.group_by_clause.expressions)
                having_node = ComparisonNode(random.choice(['=', '<>', '<', '>']))
                having_node.add_child(expr)
                having_node.add_child(LiteralNode(random.randint(1, 100), "INT"))
            else:
                # Create a condition based on aggregate function
                count_func = next(f for f in self.functions if f.name == "COUNT")
                func_node = FunctionCallNode(count_func)
                col_ref = self._get_random_column_reference()
                if col_ref:
                    func_node.add_child(col_ref)
                    having_node = ComparisonNode('>')
                    having_node.add_child(func_node)
                    having_node.add_child(LiteralNode(0, "INT"))

        self.having_clause = having_node
        if having_node:
            self.add_child(having_node)

    def _is_valid_having_clause(self, having_node: ASTNode) -> bool:
        """Validate if the HAVING clause is valid"""
        if not having_node:
            return True

        # HAVING clause must contain aggregate functions or reference columns from GROUP BY
        has_aggregate = having_node.contains_aggregate_function()

        if not has_aggregate and self.group_by_clause and hasattr(self.group_by_clause, 'expressions'):
            # Get all column references from GROUP BY
            group_by_columns = set()
            for expr in self.group_by_clause.expressions:
                group_by_columns.update(expr.get_referenced_columns())

            # Get all column references from HAVING clause
            having_columns = having_node.get_referenced_columns()

            # Check if all columns in HAVING are in GROUP BY
            if not having_columns.issubset(group_by_columns):
                return False

        return has_aggregate or (self.group_by_clause and hasattr(self.group_by_clause, 'expressions') and len(
            self.group_by_clause.expressions) > 0)

    def _get_random_column_reference(self) -> Optional[ColumnReferenceNode]:
        """Get a random column reference, preferring unused columns with column_tracker"""
        if not self.from_clause or not hasattr(self.from_clause, 'get_table_alias_map'):
            return None

        table_alias_map = self.from_clause.get_table_alias_map()
        if not table_alias_map:
            return None

        table_name = random.choice(list(table_alias_map.keys()))
        table = next((t for t in self.tables if t.name == table_name), None)
        if not table:
            return None

        table_alias = table_alias_map[table_name]
        
        # If column_tracker exists, use it to select unused columns
        if self.column_tracker:
            column = get_random_column_with_tracker(table, table_alias, self.column_tracker, for_select=True)
            if column:
                return ColumnReferenceNode(column, table_alias)

        # If no column_tracker or no available columns, fall back to original logic
        column = random.choice(table.columns)
        return ColumnReferenceNode(column, table_alias)

    def set_order_by_clause(self, order_by_node: OrderByNode) -> None:
        self.order_by_clause = order_by_node
        if order_by_node:
            self.add_child(order_by_node)

    def set_limit_clause(self, limit_node: LimitNode) -> None:
        self.limit_clause = limit_node
        if limit_node:
            self.add_child(limit_node)
    
    def set_for_update(self, mode) -> None:
        """Set lock mode
        Parameters:
            mode: Lock mode, allowed values: 'update', 'share', 'no key update', 'key share'
        """
        valid_modes = ['update', 'share', 'no key update', 'key share']
        if mode in valid_modes:
            self.for_update = mode
        else:
            # Default to FOR UPDATE
            self.for_update = 'update'

    def to_sql(self) -> str:
        parts = []

        # SELECT clause - ensure at least one expression
        select_parts = []
        for expr, alias in self.select_expressions:
            expr_sql = expr.to_sql()
            # Use auto-generated alias if no explicit alias provided
            if alias:
                select_parts.append(f"{expr_sql} AS {alias}")
            else:
                # Generate default alias for expressions without alias
                idx = self.select_expressions.index((expr, alias)) + 1
                default_alias = f"col_{idx}"
                select_parts.append(f"{expr_sql} AS {default_alias}")

        # Prevent empty SELECT clause
        if not select_parts:
            select_parts.append("1 AS col_1")  # Add default expression to avoid syntax error

        distinct_str = "DISTINCT " if self.distinct else ""
        parts.append(f"SELECT {distinct_str}{', '.join(select_parts)}")

        # FROM clause
        if self.from_clause:
            parts.append(f"FROM {self.from_clause.to_sql()}")
        else:
            parts.append("FROM DUAL")

        # WHERE clause
        if self.where_clause:
            where_sql = self.where_clause.to_sql()
            if where_sql:  # Only add valid conditions
                parts.append(f"WHERE {where_sql}")

        # GROUP BY clause (ensure not empty)
        if self.group_by_clause:
            group_by_sql = self.group_by_clause.to_sql()
            if group_by_sql.strip():  # Filter out empty GROUP BY
                parts.append(f"GROUP BY {group_by_sql}")

        # HAVING clause - add only when there is GROUP BY
        if self.having_clause and self.group_by_clause:
            having_sql = self.having_clause.to_sql()
            if having_sql:  # Only add valid conditions
                parts.append(f"HAVING {having_sql}")

        # ORDER BY clause
        if self.order_by_clause:
            order_by_sql = self.order_by_clause.to_sql()
            if order_by_sql:  # Only add valid ordering
                parts.append(f"ORDER BY {order_by_sql}")

        # LIMIT clause
        if self.limit_clause:
            parts.append(f"LIMIT {self.limit_clause.to_sql()}")
            
        # Lock mode section
        if self.for_update:
            # Get current database dialect instance
            current_dialect = get_current_dialect()
            dialect_name = current_dialect.name.lower()
            lock_clause = ""
            # Check if it's Percona dialect
            is_percona = 'percona' in dialect_name or (hasattr(current_dialect, '__class__') and 'percona' in current_dialect.__class__.__name__.lower())
            
            # For Percona 5.7, only FOR UPDATE and LOCK IN SHARE MODE are supported, not FOR NO KEY UPDATE or other advanced lock modes
            if is_percona or dialect_name in ['mysql', 'mariadb', 'tidb', 'oceanbase', 'polardb']:
                # Percona/MySQL/MariaDB/PolarDB dialects only support FOR UPDATE and LOCK IN SHARE MODE
                if self.for_update == 'share':
                    # Check if current dialect supports SHARE lock mode
                    if hasattr(current_dialect, 'supports_share_lock_mode'):
                        if current_dialect.supports_share_lock_mode():
                            lock_clause = "LOCK IN SHARE MODE"
                        # If share lock mode is not supported, don't add lock clause
                    else:
                        # If dialect doesn't implement this method, default to LOCK IN SHARE MODE
                        lock_clause = "LOCK IN SHARE MODE"
                elif self.for_update == 'key share':
                    # For key share mode, use lock in share mode as alternative in unsupported dialects
                    if hasattr(current_dialect, 'supports_share_lock_mode'):
                        if current_dialect.supports_share_lock_mode():
                            lock_clause = "LOCK IN SHARE MODE"
                        # If share lock mode is not supported, don't add lock clause
                    else:
                        # If dialect doesn't implement this method, default to LOCK IN SHARE MODE
                        lock_clause = "LOCK IN SHARE MODE"
                elif self.for_update == 'no key update':
                    # For no key update mode, use for update as alternative in unsupported dialects
                    lock_clause = "FOR UPDATE"
                else:
                    # For update mode, use FOR UPDATE
                    lock_clause = "FOR UPDATE"
            else:
                # PostgreSQL and other dialects support all modes
                lock_clause = f"FOR {self.for_update.upper()}"
                
            if lock_clause:
                parts.append(lock_clause)
                
        return " ".join(parts)

    def collect_table_aliases(self) -> Set[str]:
        """Collect all referenced table aliases in the SELECT statement"""
        aliases = set()

        # Collect table aliases in SELECT expressions
        for expr, _ in self.select_expressions:
            aliases.update(expr.collect_table_aliases())

        # Collect table aliases in WHERE clause
        if self.where_clause:
            aliases.update(self.where_clause.collect_table_aliases())

        # Collect table aliases in GROUP BY clause
        if self.group_by_clause:
            aliases.update(self.group_by_clause.collect_table_aliases())

        # Collect table aliases in HAVING clause
        if self.having_clause:
            aliases.update(self.having_clause.collect_table_aliases())

        # Collect table aliases in ORDER BY clause
        if self.order_by_clause:
            aliases.update(self.order_by_clause.collect_table_aliases())

        return aliases

    def get_defined_aliases(self) -> Set[str]:
        """Get all table aliases defined in this SELECT statement (including subqueries)"""
        aliases = set()

        # Get defined table aliases from FROM clause
        if self.from_clause and hasattr(self.from_clause, 'get_defined_aliases'):
            aliases.update(self.from_clause.get_defined_aliases())

        return aliases

    def validate_all_columns(self) -> Tuple[bool, List[str]]:
        """Validate all column references are valid"""
        errors = []

        if not self.from_clause:
            return (False, ["Missing FROM clause"])

        # Collect all referenced table aliases
        referenced_aliases = set()
        for expr, _ in self.select_expressions:
            referenced_aliases.update(expr.collect_table_aliases())

        # Collect table aliases defined in FROM clause
        defined_aliases = self.from_clause.get_all_aliases()
        # Check if any referenced table alias is not defined in FROM clause
        undefined_aliases = referenced_aliases - defined_aliases
        if undefined_aliases:
            errors.extend([f"Table alias '{alias}' referenced in SELECT clause is not defined in FROM clause" for alias in undefined_aliases])

        # Validate SELECT expressions
        for expr, _ in self.select_expressions:
            if hasattr(expr, 'validate_columns'):
                valid, expr_errors = expr.validate_columns(self.from_clause)
                if not valid:
                    errors.extend(expr_errors)

        # Validate WHERE clause
        if self.where_clause and hasattr(self.where_clause, 'validate_columns'):
            # Check if WHERE clause contains subquery
            has_subquery = False
            subquery_node = None
            
            # Check if it's an EXISTS/NOT EXISTS subquery
            if hasattr(self.where_clause, 'operator') and self.where_clause.operator in ['EXISTS', 'NOT EXISTS']:
                has_subquery = True
                if len(self.where_clause.children) > 0:
                    subquery_node = self.where_clause.children[0]
            # Check if it's a subquery in comparison operator (e.g., <>, =, <, >, etc.)
            elif hasattr(self.where_clause, 'children'):
                for child in self.where_clause.children:
                    if isinstance(child, SubqueryNode):
                        has_subquery = True
                        subquery_node = child
                        break
            
            # If subquery is included, let the subquery validate its own internal column references
            if has_subquery and subquery_node and hasattr(subquery_node, 'validate_inner_columns'):
                valid, where_errors = subquery_node.validate_inner_columns()
                if not valid:
                    errors.extend(where_errors)
            else:
                # Regular WHERE clause validation
                valid, where_errors = self.where_clause.validate_columns(self.from_clause)
                if not valid:
                    errors.extend(where_errors)

        # Validate GROUP BY clause
        if self.group_by_clause:
            for expr in self.group_by_clause.expressions:
                if hasattr(expr, 'validate_columns'):
                    valid, expr_errors = expr.validate_columns(self.from_clause)
                    if not valid:
                        errors.extend(expr_errors)

        # Validate HAVING clause
        if self.having_clause and hasattr(self.having_clause, 'validate_columns'):
            valid, having_errors = self.having_clause.validate_columns(self.from_clause)
            if not valid:
                errors.extend(having_errors)

        # Validate column references in ON clause
        if hasattr(self.from_clause, 'joins'):
            for join in self.from_clause.joins:
                condition = join.get('condition')
                if condition and hasattr(condition, 'validate_columns'):
                    valid, join_errors = condition.validate_columns(self.from_clause)
                    if not valid:
                        errors.extend([f"JOIN condition error: {err}" for err in join_errors])

        return (len(errors) == 0, errors)

    def repair_invalid_columns(self) -> None:
        """Repair all invalid column references"""
        if not self.from_clause:
            return

        # Fix duplicate column aliases
        aliases = []
        alias_count = {}
        new_select_expressions = []
        for i, (expr, alias) in enumerate(self.select_expressions):
            # Generate default alias for expressions without explicit alias
            current_alias = alias if alias else f"col_{i + 1}"
            
            # Check if alias is duplicated
            if current_alias in alias_count:
                # Generate new unique alias
                alias_count[current_alias] += 1
                new_alias = f"{current_alias}_{alias_count[current_alias]}"
            else:
                alias_count[current_alias] = 1
                new_alias = current_alias
            
            new_select_expressions.append((expr, new_alias))
            aliases.append(new_alias)
        
        # Update SELECT expressions
        self.select_expressions = new_select_expressions

        # Fix SELECT expressions
        for i, (expr, alias) in enumerate(self.select_expressions):
            if hasattr(expr, 'repair_columns'):
                expr.repair_columns(self.from_clause)

        # Fix WHERE clause
        if self.where_clause and hasattr(self.where_clause, 'repair_columns'):
            # Check if WHERE clause contains subqueries, similar to logic in validate_all_columns
            has_subquery = False
            subquery_node = None
            
            # Check if it's an EXISTS/NOT EXISTS subquery
            if hasattr(self.where_clause, 'operator') and self.where_clause.operator in ['EXISTS', 'NOT EXISTS']:
                has_subquery = True
                if len(self.where_clause.children) > 0:
                    subquery_node = self.where_clause.children[0]
            # Check if it's a subquery in comparison operator (e.g., <>, =, <, >, etc.)
            elif hasattr(self.where_clause, 'children'):
                for child in self.where_clause.children:
                    if isinstance(child, SubqueryNode):
                        has_subquery = True
                        subquery_node = child
                        break
            
            # If subquery is included, let the subquery repair its own internal column references
            if has_subquery and subquery_node and hasattr(subquery_node, 'repair_columns'):
                # Important: Pass None as from_node parameter to ensure subquery's repair_columns method is completely isolated
                # Use subquery's own FROM clause for internal column reference repair, not the outer query's FROM clause
                subquery_node.repair_columns(None)
                # For other parts of WHERE clause (non-subquery parts), still use outer from_clause for repair
                remaining_children = [child for child in self.where_clause.children if child != subquery_node]
                for child in remaining_children:
                    if hasattr(child, 'repair_columns'):
                        child.repair_columns(self.from_clause)
                    elif isinstance(child, ColumnReferenceNode) and hasattr(child, 'is_valid') and not child.is_valid(self.from_clause):
                        if hasattr(child, 'find_replacement'):
                            replacement = child.find_replacement(self.from_clause)
                            if replacement:
                                for i, c in enumerate(self.where_clause.children):
                                    if c == child:
                                        self.where_clause.children[i] = replacement
                                        break
            else:
                # Regular WHERE clause repair
                self.where_clause.repair_columns(self.from_clause)

        # Repair GROUP BY clause
        if self.group_by_clause:
            for expr in self.group_by_clause.expressions:
                if hasattr(expr, 'repair_columns'):
                    expr.repair_columns(self.from_clause)

        # Repair HAVING clause
        if self.having_clause and hasattr(self.having_clause, 'repair_columns'):
            self.having_clause.repair_columns(self.from_clause)

        # Repair column references in ON clause
        if hasattr(self.from_clause, 'joins'):
            for join in self.from_clause.joins:
                condition = join.get('condition')
                if condition and hasattr(condition, 'repair_columns'):
                    condition.repair_columns(self.from_clause)
    
    def contains_window_function(self) -> bool:
        """Check if contains window functions"""
        # Check SELECT expressions
        for expr, _ in self.select_expressions:
            if hasattr(expr, 'contains_window_function') and expr.contains_window_function():
                return True
        
        # Check WHERE clause
        if self.where_clause and hasattr(self.where_clause, 'contains_window_function') and self.where_clause.contains_window_function():
            return True
        
        # Check HAVING clause
        if self.having_clause and hasattr(self.having_clause, 'contains_window_function') and self.having_clause.contains_window_function():
            return True
        
        return False
        
    def contains_aggregate_function(self) -> bool:
        """Check if contains aggregate functions"""
        # Check SELECT expressions
        for expr, _ in self.select_expressions:
            if hasattr(expr, 'contains_aggregate_function') and expr.contains_aggregate_function():
                return True
            # Check function call node
            if isinstance(expr, FunctionCallNode) and expr.metadata.get('func_type') == 'aggregate':
                return True
        
        # Check HAVING clause
        if self.having_clause:
            if hasattr(self.having_clause, 'contains_aggregate_function') and self.having_clause.contains_aggregate_function():
                return True
            # Check function call node
            if isinstance(self.having_clause, FunctionCallNode) and self.having_clause.metadata.get('func_type') == 'aggregate':
                return True
        
        return False
        
    def get_referenced_columns(self) -> Set[str]:
        """Get all referenced columns"""
        columns = set()
        # Collect column references in SELECT expressions
        for expr, _ in self.select_expressions:
            if hasattr(expr, 'get_referenced_columns'):
                columns.update(expr.get_referenced_columns())
        
        # Collect column references in WHERE clause
        if self.where_clause and hasattr(self.where_clause, 'get_referenced_columns'):
            columns.update(self.where_clause.get_referenced_columns())
        
        # Collect column references in GROUP BY clause
        if self.group_by_clause:
            for expr in self.group_by_clause.expressions:
                if hasattr(expr, 'get_referenced_columns'):
                    columns.update(expr.get_referenced_columns())
        
        # Collect column references in HAVING clause
        if self.having_clause and hasattr(self.having_clause, 'get_referenced_columns'):
            columns.update(self.having_clause.get_referenced_columns())
        
        # Collect column references in ORDER BY clause
        if self.order_by_clause:
            for expr in self.order_by_clause.expressions:
                if hasattr(expr, 'get_referenced_columns'):
                    columns.update(expr.get_referenced_columns())
        
        return columns