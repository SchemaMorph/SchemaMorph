# GroupByNode class definition - GROUP BY clause node
from typing import Set, List
from .ast_node import ASTNode
from data_structures.node_type import NodeType

class GroupByNode(ASTNode):
    """GROUP BY clause node"""

    def __init__(self):
        super().__init__(NodeType.GROUP_BY)
        self.expressions: List[ASTNode] = []  # List of grouping expressions

    def add_expression(self, expr: ASTNode) -> None:
        self.expressions.append(expr)
        self.add_child(expr)

    def to_sql(self) -> str:
        if not self.expressions:
            return ""
        return ", ".join(expr.to_sql() for expr in self.expressions)

    def collect_table_aliases(self) -> Set[str]:
        aliases = set()
        for expr in self.expressions:
            aliases.update(expr.collect_table_aliases())
        return aliases