# OrderByNode class definition - ORDER BY clause node
from typing import List, Tuple
from .ast_node import ASTNode
from data_structures.node_type import NodeType

class OrderByNode(ASTNode):
    """ORDER BY clause node"""

    def __init__(self):
        super().__init__(NodeType.ORDER_BY)
        self.expressions: List[Tuple[ASTNode, str]] = []  # (expression, direction)

    def add_expression(self, expr: ASTNode, direction: str = 'ASC') -> None:
        self.expressions.append((expr, direction))
        self.add_child(expr)

    def to_sql(self) -> str:
        if not self.expressions:
            return ""
        parts = []
        for expr, direction in self.expressions:
            parts.append(f"{expr.to_sql()} {direction}")
        return ", ".join(parts)