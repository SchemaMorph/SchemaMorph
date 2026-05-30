# ColumnReferenceNode class definition - column reference node
from typing import Set, Optional
import random
from .ast_node import ASTNode
from data_structures.node_type import NodeType
from data_structures.column import Column
from data_structures.table import Table

class ColumnReferenceNode(ASTNode):
    """Column reference node"""

    def __init__(self, column: Column, table_alias: str):
        super().__init__(NodeType.COLUMN_REFERENCE)
        self.column = column
        self.table_alias = table_alias
        self.metadata = {
            'column_name': column.name,
            'table_name': column.table_name,
            'table_alias': table_alias,
            'data_type': column.data_type,
            'category': column.category,
            'is_aggregate': False  # Column reference is not aggregate
        }

    def to_sql(self) -> str:
        return f"{self.table_alias}.{self.column.name}"

    def collect_table_aliases(self) -> Set[str]:
        """Return the table alias used by this column reference"""
        return {self.table_alias}

    def collect_column_aliases(self) -> Set[str]:
        """Return the column name used by this column reference (may be an alias)"""
        return {self.column.name}

    def is_valid(self, from_node: 'FromNode') -> bool:
        """Check if the column reference is valid"""
        table_ref = from_node.get_table_for_alias(self.table_alias)
        if not table_ref:
            return False  # Table alias is invalid

        if isinstance(table_ref, Table):
            # Check if the table contains this column
            return table_ref.has_column(self.column.name)
        else:
            # Local import within method to avoid circular imports
            from .subquery_node import SubqueryNode
            if isinstance(table_ref, SubqueryNode):
                # Check if the subquery contains this column alias
                return table_ref.has_column_alias(self.column.name)
        return False

    def find_replacement(self, from_node: 'FromNode') -> Optional['ColumnReferenceNode']:
        """Find a valid replacement column reference, supporting subquery column aliases"""
        table_ref = from_node.get_table_for_alias(self.table_alias)
        if not table_ref:
            # Table alias is invalid, try to find a valid table alias replacement
            valid_aliases = list(from_node.get_all_aliases())
            if not valid_aliases:
                return None
            new_alias = random.choice(valid_aliases)
            table_ref = from_node.get_table_for_alias(new_alias)
            if not table_ref:
                return None
            self.table_alias = new_alias

        if isinstance(table_ref, Table):
            # Find similar columns from tables
            similar_cols = table_ref.get_similar_columns(self.column.name)
            if similar_cols:
                return ColumnReferenceNode(random.choice(similar_cols), self.table_alias)
            return ColumnReferenceNode(table_ref.get_random_column(), self.table_alias)

        else:
            # Local import within method to avoid circular imports
            from .subquery_node import SubqueryNode
            if isinstance(table_ref, SubqueryNode):
                # Fix: Only use aliases actually defined in the subquery
                if table_ref.column_alias_map:
                    # Get all available column aliases
                    valid_aliases = list(table_ref.column_alias_map.keys())
                    if valid_aliases:
                        alias = random.choice(valid_aliases)
                        col_name, data_type, category = table_ref.column_alias_map[alias]
                        virtual_col = Column(
                            name=alias,
                            data_type=data_type,
                            category=category,
                            is_nullable=False,
                            table_name=table_ref.alias
                        )
                        return ColumnReferenceNode(virtual_col, self.table_alias)

                # If no alias or invalid alias, use default column
                virtual_col = Column(
                    name="id",
                    data_type="INT",
                    category="numeric",
                    is_nullable=False,
                    table_name=table_ref.alias
                )
                return ColumnReferenceNode(virtual_col, self.table_alias)

        return None