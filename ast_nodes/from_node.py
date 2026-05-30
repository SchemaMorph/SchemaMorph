# FromNode class definition - FROM clause node
import random
from typing import List, Union, Dict, Set, Optional, Tuple
from .ast_node import ASTNode
from data_structures.node_type import NodeType
from data_structures.table import Table
from data_structures.db_dialect import get_current_dialect
from .subquery_node import SubqueryNode
from .comparison_node import ComparisonNode
from .column_reference_node import ColumnReferenceNode


class FromNode(ASTNode):
    """FROM clause node, handles table references and joins"""

    def __init__(self):
        super().__init__(NodeType.FROM)
        # Keep original lists for backward compatibility
        self.table_references: List[Union[Table, SubqueryNode]] = []
        self.aliases: List[str] = []
        self.joins: List[Dict] = []  # Join information: {type, table, alias, condition}
        
        # Add new dictionaries for more reliable table-alias mapping management
        self.alias_to_table: Dict[str, Union[Table, SubqueryNode]] = {}
        self.table_to_alias: Dict[int, str] = {}  # Use id as key to avoid object comparison issues

    def add_table(self, table: Union[Table, SubqueryNode], alias: str) -> None:
        """Add table or subquery reference while updating all mapping structures"""
        self.table_references.append(table)
        self.aliases.append(alias)
        
        # Update new dictionary mapping
        self.alias_to_table[alias] = table
        self.table_to_alias[id(table)] = alias

    def add_join(self, join_type: str, table: Union[Table, SubqueryNode], alias: str, condition: ASTNode) -> None:
        """Add join while updating all mapping structures"""
        self.joins.append({
            'type': join_type,
            'table': table,
            'alias': alias,
            'condition': condition
        })
        self.table_references.append(table)
        self.aliases.append(alias)
        self.add_child(condition)
        
        # Update new dictionary mapping
        self.alias_to_table[alias] = table
        self.table_to_alias[id(table)] = alias

    def get_table_for_alias(self, alias: str) -> Optional[Union[Table, SubqueryNode]]:
        """Get table or subquery by alias, prioritizing dictionary mapping"""
        # First try to get from new dictionary mapping
        if alias in self.alias_to_table:
            return self.alias_to_table[alias]
        
        # Keep original logic as fallback
        try:
            index = self.aliases.index(alias)
            return self.table_references[index]
        except ValueError:
            return None

    def get_alias_for_table(self, table_or_name: Union[Table, SubqueryNode, str]) -> Optional[str]:
        """Get alias by table object or table name, supporting more input types and prioritizing dictionary mapping"""
        # If input is a table object, first try to use new dictionary mapping
        if isinstance(table_or_name, (Table, SubqueryNode)):
            table_id = id(table_or_name)
            if table_id in self.table_to_alias:
                return self.table_to_alias[table_id]
        
        # If input is a string (table name or subquery alias)
        elif isinstance(table_or_name, str):
            # Iterate through dictionary mapping to find match
            for alias, table in self.alias_to_table.items():
                if isinstance(table, Table) and table.name == table_or_name:
                    return alias
                elif isinstance(table, SubqueryNode) and table.alias == table_or_name:
                    return alias
        
        # Keep original logic as fallback
        table_name = table_or_name if isinstance(table_or_name, str) else None
        if table_name:
            for i, ref in enumerate(self.table_references):
                if isinstance(ref, Table) and ref.name == table_name:
                    return self.aliases[i]
                elif isinstance(ref, SubqueryNode) and ref.alias == table_name:
                    return self.aliases[i]
        
        return None

    def get_all_aliases(self) -> Set[str]:
        """Get all table aliases"""
        return set(self.aliases)

    def get_table_alias_map(self) -> Dict[str, str]:
        """Get table name to alias mapping, using new dictionary mapping implementation"""
        mapping = {}
        
        # Prioritize using new dictionary mapping to build the result
        for alias, table in self.alias_to_table.items():
            if isinstance(table, Table):
                mapping[table.name] = alias
            elif isinstance(table, SubqueryNode):
                mapping[table.alias] = alias
        
        # If new mapping has no data, use original logic as fallback
        if not mapping:
            for i, ref in enumerate(self.table_references):
                if isinstance(ref, Table):
                    mapping[ref.name] = self.aliases[i]
                elif isinstance(ref, SubqueryNode):
                    mapping[ref.alias] = self.aliases[i]
        
        return mapping

    def to_sql(self) -> str:
        if not self.table_references:
            return ""

        # Main table
        parts = []
        first_table = self.table_references[0]
        first_alias = self.aliases[0]

        if isinstance(first_table, Table):
            table_sql = f"{first_table.name} AS {first_alias}"
            # Add index hint
            table_sql_with_hint = self._add_index_hint(table_sql, first_table)
            parts.append(table_sql_with_hint)
        else:
            parts.append(first_table.to_sql())

        # Join section
        for join in self.joins:
            join_type = join['type'].upper()
            table = join['table']
            alias = join['alias']
            condition = join.get('condition')
            
            # Support USING clause
            use_using = False
            if random.random() < 0.2 and isinstance(condition, ComparisonNode) and condition.operator == '=':
                # Check if we can convert to USING clause
                if len(condition.children) == 2 and all(isinstance(child, ColumnReferenceNode) for child in condition.children):
                    left_col = condition.children[0]
                    right_col = condition.children[1]
                    # Only use USING clause when column names are the same and belong to different tables
                    if hasattr(left_col, 'column') and hasattr(right_col, 'column') and \
                       left_col.column.name == right_col.column.name and \
                       left_col.table_alias != right_col.table_alias:
                        use_using = True
                        using_col = left_col.column.name
            
            if isinstance(table, Table):
                table_sql = f"{table.name} AS {alias}"
                # Add index hint
                table_sql = self._add_index_hint(table_sql, table)
            else:
                table_sql = table.to_sql()
            
            if use_using:
                # Use USING clause
                parts.append(f"{join_type} JOIN {table_sql} USING ({using_col})")
            elif condition:
                # Standard ON condition
                condition_sql = condition.to_sql()
                
                # Check if current dialect supports subqueries in JOIN conditions
                dialect = get_current_dialect()
                if hasattr(dialect, 'supports_subqueries_in_join') and not dialect.supports_subqueries_in_join():
                    # If dialect doesn't support subqueries in JOIN, avoid generating ON conditions with subqueries
                    # Simple check: if condition SQL contains SELECT statement (might be subquery), use simple condition
                    if 'SELECT' in condition_sql.upper() or '(' in condition_sql and ')' in condition_sql:
                        # Use a simple condition instead, like 1=1 or table primary key = primary key
                        # We use 1=1 as replacement here
                        parts.append(f"{join_type} JOIN {table_sql} ON 1=1")
                    else:
                        parts.append(f"{join_type} JOIN {table_sql} ON {condition_sql}")
                else:
                    # Dialect supports subqueries in JOIN, generate normally
                    parts.append(f"{join_type} JOIN {table_sql} ON {condition_sql}")
            else:
                # No condition (like CROSS JOIN)
                parts.append(f"{join_type} JOIN {table_sql}")

        return " ".join(parts)
        
    def add_outer_join(self, table: Union[Table, SubqueryNode], alias: str, condition: ASTNode) -> None:
        """Add outer join (LEFT/RIGHT/FULL)"""
        outer_join_type = random.choice(['LEFT OUTER', 'RIGHT OUTER', 'FULL OUTER'])
        self.add_join(outer_join_type, table, alias, condition)
        
    def add_cross_join(self, table: Union[Table, SubqueryNode], alias: str) -> None:
        """Add cross join"""
        self.joins.append({
            'type': 'CROSS',
            'table': table,
            'alias': alias,
            'condition': None
        })
        self.table_references.append(table)
        self.aliases.append(alias)

    def validate_table_references(self) -> Tuple[bool, List[str]]:
        """Validate if table references are valid"""
        errors = []
        # Table reference validation logic can be added here
        return (len(errors) == 0, errors)

    def _add_index_hint(self, table_sql: str, table: Table) -> str:
        """Add index hint for table
        
        Args:
            table_sql: SQL representation of the table
            table: Table object
            
        Returns:
            Original table SQL (no index hints added anymore)
        """
        # Remove all index hints as per requirements
        return table_sql
    
    def repair_table_references(self) -> None:
        """Repair invalid table references"""
        # Table reference repair logic can be added here
        pass

    def get_defined_aliases(self) -> Set[str]:
        """Get all defined aliases"""
        aliases = set(self.aliases)
        # Collect aliases from subqueries
        for ref in self.table_references:
            if isinstance(ref, SubqueryNode):
                aliases.update(ref.get_defined_aliases())
        return aliases