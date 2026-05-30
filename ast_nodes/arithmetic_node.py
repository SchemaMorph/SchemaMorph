# ArithmeticNode class definition - Arithmetic expression node
from typing import Set, Tuple, List
from .ast_node import ASTNode
from data_structures.node_type import NodeType

class ArithmeticNode(ASTNode):
    """Arithmetic expression node (with division by zero protection)"""

    def __init__(self, operator: str):
        super().__init__(NodeType.ARITHMETIC)
        self.operator = operator
        self.metadata = {
            'operator': operator,
            'is_aggregate': False  # Arithmetic expressions are not aggregates
        }

    def to_sql(self) -> str:
        if len(self.children) != 2:
            return ""

        left = self.children[0].to_sql()
        right = self.children[1].to_sql()

        # Add division by zero protection for division and modulo operations
        if self.operator in ['/', '%']:
            return f"({left} {self.operator} NULLIF({right}, 0))"
        return f"({left} {self.operator} {right})"

    def collect_column_aliases(self) -> Set[str]:
        """Collect column aliases referenced in the arithmetic expression"""
        aliases = set()
        for child in self.children:
            aliases.update(child.collect_column_aliases())
        return aliases

    def validate_columns(self, from_node: 'FromNode') -> Tuple[bool, List[str]]:
        """Validate if column references in the arithmetic expression are valid"""
        errors = []
        for child in self.children:
            if hasattr(child, 'validate_columns'):
                valid, child_errors = child.validate_columns(from_node)
                if not valid:
                    errors.extend(child_errors)
            elif isinstance(child, ColumnReferenceNode):
                if not child.is_valid(from_node):
                    errors.append(f"Invalid column reference: {child.to_sql()}")
        return (len(errors) == 0, errors)

    def repair_columns(self, from_node: 'FromNode') -> None:
        """Repair invalid column references in the arithmetic expression"""
        for i, child in enumerate(self.children):
            if hasattr(child, 'repair_columns'):
                child.repair_columns(from_node)
            elif isinstance(child, ColumnReferenceNode) and not child.is_valid(from_node):
                replacement = child.find_replacement(from_node)
                if replacement:
                    self.children[i] = replacement