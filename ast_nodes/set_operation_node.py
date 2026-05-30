# SetOperationNode class definition - Set operation node
from typing import List, Set
from .ast_node import ASTNode
from data_structures.node_type import NodeType

class SetOperationNode(ASTNode):
    """Set operation node (UNION/UNION ALL/INTERSECT/EXCEPT)"""

    def __init__(self, operation_type: str):
        super().__init__(NodeType.SET_OPERATION)
        self.operation_type = operation_type  # 'UNION', 'UNION ALL', 'INTERSECT', 'EXCEPT'
        self.queries: List['SelectNode'] = []  # SELECT queries involved in the set operation

    def add_query(self, select_node: 'SelectNode') -> None:
        """Add SELECT query to participate in set operation"""
        self.queries.append(select_node)
        self.add_child(select_node)

    def to_sql(self) -> str:
        """Convert to SQL string"""
        if not self.queries or len(self.queries) < 2:
            return ""  # At least two queries are required
        
        # Get current database dialect
        from data_structures.db_dialect import get_current_dialect
        current_dialect = get_current_dialect()
        is_polardb = current_dialect.__class__.__name__ == 'PolarDBDialect'
        # Check if it's Percona dialect
        is_percona = 'percona' in current_dialect.name.lower() or (hasattr(current_dialect, '__class__') and 'percona' in current_dialect.__class__.__name__.lower())
        
        # Ensure each query is a valid SQL statement
        query_sqls = []
        for query in self.queries:
            sql = query.to_sql()
            # Remove extra spaces or newlines that might cause issues
            sql = sql.strip()
            # Ensure each query is not an empty string
            if sql:
                # Check if the query contains ORDER BY or LIMIT clause, wrap with parentheses if it does
                # For PolarDB, the left query (first query) is not wrapped in parentheses
                if not is_polardb:
                    if ('ORDER BY' in sql.upper() or 'LIMIT' in sql.upper()) and not is_polardb:
                        # Ensure the query is not surrounded by parentheses
                        if not (sql.startswith('(') and sql.endswith(')')):
                            sql = f"({sql})"
                query_sqls.append(sql)
        
        # If there are no valid queries, return empty string
        if len(query_sqls) < 2:
            return ""
        
        # Special handling for Percona dialect: Percona 5.7 does not support INTERSECT and EXCEPT operators
        if is_percona:
            if self.operation_type in ['INTERSECT', 'EXCEPT']:
                # In Percona environment, convert INTERSECT and EXCEPT to UNION ALL
                # Note: This is not a semantically equivalent conversion, but it avoids syntax errors
                # A more complex conversion strategy may be needed in practical applications
                return " UNION ALL ".join(query_sqls).strip()
            elif self.operation_type == "UNION":
                # Percona supports UNION but we uniformly use UNION ALL to avoid potential issues
                return " UNION ALL ".join(query_sqls).strip()
            else:
                # For UNION ALL, keep it as is
                return " UNION ALL ".join(query_sqls).strip()
        else:
            # Normal handling for non-Percona dialects
            if self.operation_type == "UNION ALL":
                # Ensure there's only one space around the operator, with no extra spaces or newlines
                return " UNION ALL ".join(query_sqls).strip()
            else:
                # Handle other operators
                return f" {self.operation_type} ".join(query_sqls).strip()

    def contains_window_function(self) -> bool:
        """Check if contains window functions"""
        for query in self.queries:
            if query.contains_window_function():
                return True
        return False

    def contains_aggregate_function(self) -> bool:
        """Check if contains aggregate functions"""
        for query in self.queries:
            if query.contains_aggregate_function():
                return True
        return False

    def get_referenced_columns(self) -> Set[str]:
        """Get all referenced columns"""
        columns = set()
        for query in self.queries:
            columns.update(query.get_referenced_columns())
        return columns