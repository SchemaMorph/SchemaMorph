# SubqueryNode class definition - Subquery node
from typing import Set, Optional, Dict, Tuple, List
from .ast_node import ASTNode
from data_structures.node_type import NodeType
from .column_reference_node import ColumnReferenceNode
from .function_call_node import FunctionCallNode
from .case_node import CaseNode
from .arithmetic_node import ArithmeticNode

class SubqueryNode(ASTNode):
    """Subquery node - Enhanced version, manages internal column alias mapping"""

    def __init__(self, select_node: 'SelectNode', alias: str):
        super().__init__(NodeType.SUBQUERY)
        self.select_node = select_node
        self.alias = alias
        self.add_child(select_node)
        self.metadata = {
            'alias': alias,
            'is_aggregate': False  # Subquery itself is not aggregate
        }

        # Create subquery column alias mapping: alias -> (column_name, data_type, category)
        self.column_alias_map = self._build_column_alias_map(select_node)

    def _build_column_alias_map(self, select_node: 'SelectNode') -> Dict[str, Tuple[str, str, str]]:
        """Build complete subquery column alias mapping, ensuring all expressions have aliases"""
        alias_map = {}
        for i, (expr, expr_alias) in enumerate(select_node.select_expressions):
            # Generate default alias for expressions without explicit alias
            alias = expr_alias if expr_alias else f"col_{i + 1}"

            if isinstance(expr, ColumnReferenceNode):
                alias_map[alias] = (
                    expr.column.name,
                    expr.column.data_type,
                    expr.column.category
                )
            elif isinstance(expr, FunctionCallNode):
                alias_map[alias] = (
                    alias,  # Function result has no original column name
                    expr.metadata.get('return_type', 'unknown'),
                    self._get_category_from_type(expr.metadata.get('return_type', 'unknown'))
                )
            elif isinstance(expr, CaseNode):
                # Try to infer type from CASE result
                result_type = 'unknown'
                result_category = 'any'
                if expr.when_clauses:
                    first_result = expr.when_clauses[0][1]
                    result_type = first_result.metadata.get('data_type', 'unknown')
                    result_category = first_result.metadata.get('category', 'any')
                alias_map[alias] = (alias, result_type, result_category)
            elif isinstance(expr, ArithmeticNode):
                # Arithmetic operation result is typically numeric
                alias_map[alias] = (alias, 'numeric', 'numeric')
            else:
                alias_map[alias] = (
                    alias,
                    expr.metadata.get('data_type', 'unknown'),
                    expr.metadata.get('category', 'any')
                )
        return alias_map

    def _get_category_from_type(self, data_type: str) -> str:
        """Infer category from data type"""
        data_type = data_type.lower()
        if data_type in ['int', 'float', 'numeric']:
            return 'numeric'
        elif data_type in ['varchar', 'string', 'char']:
            return 'string'
        elif data_type in ['date', 'datetime', 'timestamp']:
            return 'datetime'
        elif data_type in ['boolean']:
            return 'boolean'
        return 'any'

    def to_sql(self) -> str:
        # Only add AS clause when alias is not empty
        if self.alias:
            return f"({self.select_node.to_sql()}) AS {self.alias}"
        else:
            return f"({self.select_node.to_sql()})"

    def get_defined_aliases(self) -> Set[str]:
        """Get all table aliases defined in this subquery (including internal subqueries)"""
        return {self.alias} | self.select_node.get_defined_aliases()

    def has_column_alias(self, alias: str) -> bool:
        """Check if subquery contains the specified column alias"""
        return alias in self.column_alias_map

    def get_column_alias_info(self, alias: str) -> Optional[Tuple[str, str, str]]:
        """Get column alias information: (original column name, data type, category)"""
        return self.column_alias_map.get(alias)

    def validate_inner_columns(self) -> Tuple[bool, List[str]]:
        """Validate if column references inside the subquery are valid"""
        return self.select_node.validate_all_columns()
    
    def collect_table_aliases(self) -> Set[str]:
        """Collect all table aliases referenced in the subquery"""
        return self.select_node.collect_table_aliases()
        
    def columns(self):
        """Provide columns method to avoid AttributeError"""
        return self.column_alias_map.values()
    
    def repair_columns(self, from_node: 'FromNode') -> None:
        """Repair column references inside the subquery
        Note: This method does not use the incoming from_node parameter, but uses the subquery's own FROM clause
        to avoid external modifications incorrectly affecting internal column references
        """
        # Completely isolate the column reference repair process for the subquery
        # Ensure only the subquery's own FROM clause is used, not any information from the outer query
        self.select_node.repair_invalid_columns()