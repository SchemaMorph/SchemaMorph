# LiteralNode class definition - literal node
from typing import Set, Any
from .ast_node import ASTNode
from data_structures.node_type import NodeType
from data_structures.db_dialect import get_dialect_config

class LiteralNode(ASTNode):
    """Literal node (handles single quote escaping)"""

    def __init__(self, value: Any, data_type: str):
        super().__init__(NodeType.LITERAL)
        self.value = value
        self.data_type = data_type
        self.category = data_type
        self.metadata = {
            'value': value,
            'data_type': data_type,
            'is_aggregate': False  # Literal is not an aggregate
        }

    def to_sql(self) -> str:
        # Get current dialect configuration
        dialect = get_dialect_config()
        
        # Use dialect-specific literal representation consistently, ensuring type information is preserved
        return dialect.get_literal_representation(self.value, self.data_type)

    def collect_column_aliases(self) -> Set[str]:
        """Literal does not reference column aliases"""
        return set()
    
    def collect_table_aliases(self) -> Set[str]:
        """Literal does not reference table aliases"""
        return set()