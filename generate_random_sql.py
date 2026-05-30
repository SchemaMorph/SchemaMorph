import random
import string
import os
import random
import re
from typing import List, Dict, Tuple, Optional, Set, Any, Union
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

# Add column usage tracker class
from data_structures.column import Column
from data_structures.db_dialect import get_current_dialect
def get_full_column_identifier(table_alias: str, column_name: str) -> str:
    """Generate complete column identifier"""
    return f"{table_alias}.{column_name}"

class ColumnUsageTracker:
    """Track column usage in SQL queries"""
    
    def __init__(self):
        # Store all used column identifiers
        self.used_columns = set()
        # Store column identifiers used in SELECT clause
        self.select_columns = set()
        # Store column identifiers used in filter clauses (where/having/on)
        self.filter_columns = set()
        # Store all available columns (table alias -> column list)
        self.available_columns = {}
        # Store all table reference information (table alias -> table object)
        self.table_references = {}
        
    def initialize_from_from_node(self, from_node):
        """Initialize available column information based on tables or subqueries in FROM clause
        
        Args:
            from_node: FromNode object containing table references and join information
        """
        
        # Clear existing information
        self.available_columns = {}
        self.table_references = {}
        
        # Get all table references
        if hasattr(from_node, 'table_references') and hasattr(from_node, 'aliases'):
            # Iterate through all table references and aliases
            for table_ref, alias in zip(from_node.table_references, from_node.aliases):
                self.table_references[alias] = table_ref
                
                # Process column information based on table type
                if hasattr(table_ref, 'columns'):
                    # Regular table, record all columns
                    self.available_columns[alias] = table_ref.columns
                elif hasattr(table_ref, 'column_alias_map'):
                    # Subquery table, record its output columns
                    subquery_columns = []
                    for col_alias, (col_name, data_type, category) in table_ref.column_alias_map.items():
                        # Create virtual columns to represent subquery output columns
                        subquery_columns.append(Column(col_alias, data_type, category, False, alias))
                    self.available_columns[alias] = subquery_columns
    
    def get_all_available_columns(self) -> Dict[str, List[Any]]:
        """Get available column information for all tables
        
        Returns:
            Dict: Mapping from table alias to list of available columns
        """
        all_available_columns = {}
        
        # Iterate through all table aliases
        for table_alias, columns in self.available_columns.items():
            # Filter unused columns
            available = [col for col in columns if not self.is_column_used(table_alias, col.name)]
            all_available_columns[table_alias] = available
        
        return all_available_columns
    
    def has_table(self, table_alias: str) -> bool:
        """Check if table alias exists in FROM clause
        
        Args:
            table_alias: Table alias
        
        Returns:
            bool: Whether the table alias exists
        """
        return table_alias in self.table_references
    
    def has_column(self, table_alias: str, column_name: str) -> bool:
        """Check if column exists in specified table
        
        Args:
            table_alias: Table alias
            column_name: Column name
        
        Returns:
            bool: Whether the column exists
        """
        if table_alias not in self.available_columns:
            return False
        
        # Check if column exists in table's column list
        return any(col.name == column_name for col in self.available_columns[table_alias])
    
    def get_table_by_alias(self, table_alias: str) -> Any:
        """Get table object by table alias
        
        Args:
            table_alias: Table alias
        
        Returns:
            Any: Table object or subquery object
        """
        return self.table_references.get(table_alias)
        
    def mark_column_as_used(self, table_alias: str, column_name: str) -> None:
        """Mark column as used"""
        column_id = get_full_column_identifier(table_alias, column_name)

        self.used_columns.add(column_id)
        
    def mark_column_as_select(self, table_alias: str, column_name: str) -> None:
        """Mark column as used in SELECT clause"""
        column_id = get_full_column_identifier(table_alias, column_name)

        self.used_columns.add(column_id)
        self.select_columns.add(column_id)
        
    def mark_column_as_filter(self, table_alias: str, column_name: str) -> None:
        """Mark column as used in filter clause (where/having/on)"""
        column_id = get_full_column_identifier(table_alias, column_name)

        self.used_columns.add(column_id)
        self.filter_columns.add(column_id)
        
    def mark_column_used(self, column_identifier: str) -> None:
        """Mark column as used (compatible with old interface)"""

        self.used_columns.add(column_identifier)
        
    def is_column_used(self, *args) -> bool:
        """Check if column is already used
            Supports two calling methods:
            1. is_column_used(column_identifier) - Directly pass a formatted column identifier, e.g., "table_alias.col_name"
            2. is_column_used(table_alias, column_name) - Pass table alias and column name
        """
        if len(args) == 1:
            # New calling method, directly using column_identifier as identifier
            column_id = args[0]
            result = column_id in self.used_columns

            return result
        elif len(args) == 2:
            # Old calling method, passing table alias and column name
            table_alias, column_name = args
            column_id = get_full_column_identifier(table_alias, column_name)
            result = column_id in self.used_columns

            return result
        
        # Incorrect number of parameters

        return False
        
    def is_column_in_select(self, *args) -> bool:
        """Check if column is used in select clause"""
        if len(args) == 1:
            column_id = args[0]
            result = column_id in self.select_columns

            return result
        elif len(args) == 2:
            table_alias, column_name = args
            column_id = get_full_column_identifier(table_alias, column_name)
            result = column_id in self.select_columns

            return result
        return False
        
    def is_column_in_filter(self, *args) -> bool:
        """Check if column is used in filter clause"""
        if len(args) == 1:
            column_id = args[0]
            result = column_id in self.filter_columns

            return result
        elif len(args) == 2:
            table_alias, column_name = args
            column_id = get_full_column_identifier(table_alias, column_name)
            result = column_id in self.filter_columns

            return result
        return False
        
    def is_column_available_for_filter(self, *args) -> bool:
        """Check if column is available for filter clause (not in select or filter)"""
        if len(args) == 1:
            column_id = args[0]
            result = column_id not in self.select_columns and column_id not in self.filter_columns

            return result
        elif len(args) == 2:
            table_alias, column_name = args
            column_id = get_full_column_identifier(table_alias, column_name)
            result = column_id not in self.select_columns and column_id not in self.filter_columns

            return result
        return False
        
    def get_available_columns(self, table: Any, table_alias: str) -> List[Any]:
        """Get unused columns from table"""

        available_columns = []
        
        # Prefer to use initialized available_columns information
        if table_alias in self.available_columns:

            # Filter unused columns from pre-stored column information
            available_columns = [col for col in self.available_columns[table_alias] 
                               if not self.is_column_used(table_alias, col.name)]
        else:
            # Fall back to original processing logic
            # Handle different types of table objects
            if hasattr(table, 'columns'):
                for col in table.columns:
                    if not self.is_column_used(table_alias, col.name):
                        available_columns.append(col)
            elif hasattr(table, 'column_alias_map'):
                # Handle subquery table objects

                for alias, (col_name, data_type, category) in table.column_alias_map.items():
                    if not self.is_column_used(table_alias, alias):
                        # Create virtual columns to represent subquery output columns, ensuring parameter order matches Column class definition
                        # Column constructor parameters: name, data_type, category, is_nullable, table_name
                        available_columns.append(Column(alias, data_type, category, False, table_alias))

        
        # Print available column information
        available_column_ids = []
        if available_columns:
            available_column_ids = [f"{table_alias}.{col.name}" for col in available_columns]
        
        # Correctly calculate total column count
        total_columns = len(self.available_columns.get(table_alias, [])) \
                       if table_alias in self.available_columns \
                       else len(table.columns) if hasattr(table, 'columns') \
                       else len(table.column_alias_map) if hasattr(table, 'column_alias_map') \
                       else 0
        
    
        return available_columns
        
    def get_columns_available_for_filter(self, table: Any, table_alias: str) -> List[Any]:
        """Get columns available for filter clause (not in select or filter)"""
        available_columns = []
        
        # Handle different types of table objects
        if hasattr(table, 'columns'):
            for col in table.columns:
                if self.is_column_available_for_filter(table_alias, col.name):
                    available_columns.append(col)
        elif hasattr(table, 'column_alias_map'):
            # Handle subquery table objects
            for alias, (col_name, data_type, category) in table.column_alias_map.items():
                if self.is_column_available_for_filter(table_alias, alias):
                    # Create virtual columns to represent subquery output columns
                    available_columns.append(Column(alias, data_type, category, False, table_alias))
        
        # Print available column information
        available_column_ids = []
        if available_columns:
            available_column_ids = [f"{table_alias}.{col.name}" for col in available_columns]
        
        # Correctly calculate total column count
        total_columns = len(table.columns) if hasattr(table, 'columns') else len(table.column_alias_map) if hasattr(table, 'column_alias_map') else 0
    
        return available_columns
        
    def select_unique_column(self, table: Any, table_alias: str) -> Optional[Any]:
        """Select unused column from table"""
        available_columns = self.get_available_columns(table, table_alias)
        if available_columns:
            selected_col = random.choice(available_columns)
            col_name = selected_col.name if hasattr(selected_col, 'name') else selected_col
            return selected_col
        
        # If all columns are used, return None or fallback option
        return None
        
    def select_column_for_select(self, table: Any, table_alias: str) -> Optional[Any]:
        """Select column for select clause (allow duplicates)"""
        
        # For select clause, prefer unused columns but allow used ones
        available_columns = self.get_available_columns(table, table_alias)
        
        if available_columns:
            # If there are unused columns, prefer to select them
            selected_col = random.choice(available_columns)
            col_name = selected_col.name if hasattr(selected_col, 'name') else selected_col
            return selected_col
        else:
            # If no unused columns, randomly select any column
            if hasattr(table, 'columns') and table.columns:
                selected_col = random.choice(table.columns)
                col_name = selected_col.name if hasattr(selected_col, 'name') else selected_col
                return selected_col
            elif hasattr(table, 'column_alias_map'):
                valid_aliases = list(table.column_alias_map.keys())
                if valid_aliases:
                    alias = random.choice(valid_aliases)
                    col_name, data_type, category = table.column_alias_map[alias]
                    col = Column(alias, data_type, category, False, table_alias)
                    return col
        
        return None
        
    def select_column_for_filter(self, table: Any, table_alias: str) -> Optional[Any]:
        """Select column for filter clause (not in select or filter)"""
        
        # For filter clause, can only select columns not in select or filter
        available_columns = self.get_columns_available_for_filter(table, table_alias)
        
        if available_columns:
            selected_col = random.choice(available_columns)
            col_name = selected_col.name if hasattr(selected_col, 'name') else selected_col
            return selected_col
        
        # If no available columns, return None
        return None

# Modify get_random_column method to support column usage tracker
def get_random_column_with_tracker(table: Any, table_alias: str, column_tracker: ColumnUsageTracker = None, for_select: bool = False) -> Optional[Any]:
    """Select column based on column usage tracker
table: table object
table_alias: table alias
column_tracker: column tracker instance
for_select: whether to select column for select clause
    """
    
    if column_tracker:
        
        if for_select:
            # Select column for select clause (allow duplicates)
            col = column_tracker.select_column_for_select(table, table_alias)
            if col:
                column_tracker.mark_column_as_select(table_alias, col.name)
                return col
        else:
            # Select column for filter clause (not in select or filter)
            col = column_tracker.select_column_for_filter(table, table_alias)
            if col:
                column_tracker.mark_column_as_filter(table_alias, col.name)
                return col
            
            # If no columns available for filter, use fallback (select any column)
            
            if hasattr(table, 'columns') and table.columns:
                selected_col = random.choice(table.columns)
                column_tracker.mark_column_as_filter(table_alias, selected_col.name)
                return selected_col
            elif hasattr(table, 'column_alias_map'):
                valid_aliases = list(table.column_alias_map.keys())
                if valid_aliases:
                    alias = random.choice(valid_aliases)
                    col_name, data_type, category = table.column_alias_map[alias]
                    column_tracker.mark_column_as_filter(table_alias, alias)
                    return Column(alias, data_type, category, False, table_alias)
        
        return None
    
    # If no tracker, use original logic
    if hasattr(table, 'get_random_column'):
        return table.get_random_column()
    elif hasattr(table, 'columns') and table.columns:
        return random.choice(table.columns)
    elif hasattr(table, 'column_alias_map'):
        valid_aliases = list(table.column_alias_map.keys())
        if valid_aliases:
            alias = random.choice(valid_aliases)
            col_name, data_type, category = table.column_alias_map[alias]
            return Column(alias, data_type, category, False, table_alias)
    
    return None

# Import AST nodes
from ast_nodes import (
    ASTNode,
    ArithmeticNode,
    CaseNode,
    ColumnReferenceNode,
    ComparisonNode,
    FromNode,
    FunctionCallNode,
    GroupByNode,
    LiteralNode,
    LogicalNode,
    OrderByNode,
    SetOperationNode,
    SubqueryNode,
    WithNode,
    LimitNode,
    SelectNode
)

# Import data structures
from data_structures.table import Table
from data_structures.column import Column
from data_structures.function import Function
from data_structures.dependency import Dependency
from data_structures.db_dialect import DBDialect, set_dialect, get_dialect_config, get_current_dialect

# Global variable to control subquery depth
SUBQUERY_DEPTH = 2

# Global variable to store table structure information for determining aggregate function parameter types during mutation
TABLES = None


def get_tables():
    """Get global table structure information
    
    Returns:
        List[Table]: table structure list, returns empty list if not initialized
    """
    global TABLES
    return TABLES if TABLES is not None else []


def set_tables(tables_list):
    """Set global table structure information
    
    Parameters:
        tables_list: table structure list
    """
    global TABLES
    TABLES = tables_list


def create_select_subquery(tables: List[Table], functions: List[Function], 
                          current_depth: int = 0, max_depth: int = 2) -> SubqueryNode:
    """Create subquery expression for SELECT clause
    
    Args:
        tables: available tables list
        functions: available functions list
        current_depth: current subquery depth
        max_depth: maximum allowed subquery depth
    
    Returns:
        SubqueryNode: subquery node usable in SELECT clause
    """
    if current_depth >= max_depth or not tables:
        return None
    
    # Create SELECT node for subquery
    subquery_select = SelectNode()
    subquery_select.tables = tables
    subquery_select.functions = functions
    
    # Select a table for subquery
    subquery_table = random.choice(tables)
    
    # Generate FROM clause for subquery
    subquery_from = FromNode()
    subquery_inner_alias = 's' + str(random.randint(100, 999))
    subquery_from.add_table(subquery_table, subquery_inner_alias)
    subquery_select.set_from_clause(subquery_from)
    
    # Generate SELECT expression for subquery (simple single-column query)
    subquery_col = subquery_table.get_random_column()

    subquery_expr = ColumnReferenceNode(subquery_col, subquery_inner_alias)
    
    # 50% probability to use aggregate function in subquery
    is_aggregate = False
    if random.random() > 0.5 and functions:
        agg_funcs = [f for f in functions if f.func_type == 'aggregate']
        if agg_funcs:
            func = random.choice(agg_funcs)
            func_node = FunctionCallNode(func)
            func_node.add_child(subquery_expr)
            subquery_expr = func_node
            is_aggregate = True
    
    subquery_select.add_select_expression(subquery_expr, 'subq_col')
    
    # When subquery is not an aggregate function, force adding LIMIT 1 and ORDER BY clauses
    if not is_aggregate:
        # Add ORDER BY clause using expressions from select clause
        from ast_nodes.order_by_node import OrderByNode
        # Get first expression from select clause as sort basis
        if hasattr(subquery_select, 'select_expressions') and subquery_select.select_expressions:
            first_expression = subquery_select.select_expressions[0]
            # Create order node and order by node
            first_expression = first_expression[0]  # Get the first parameter of the tuple, which is a ColumnReferenceNode object
            order_by_node = OrderByNode()
            order_by_node.add_expression(first_expression)
            subquery_select.set_order_by_clause(order_by_node)

        # Add LIMIT 1 clause
        from ast_nodes.limit_node import LimitNode
        limit_node = LimitNode(1)
        subquery_select.set_limit_clause(limit_node)
    
    
    # Create subquery node (using empty string as alias to ensure no AS clause is added)
    subquery_node = SubqueryNode(subquery_select, '')
    
    # Set subquery depth information
    subquery_node.metadata['depth'] = current_depth + 1
    
    # Validate subquery validity
    valid, errors = subquery_select.validate_all_columns()
    if not valid:
        # Try to fix invalid column references
        subquery_select.repair_invalid_columns()
        # Validate again
        valid, errors = subquery_select.validate_all_columns()
        
        # Return None if still invalid
        if not valid:
            return None
    
    return subquery_node


def create_simple_where_condition(table: Table, alias: str) -> Optional[ASTNode]:
    """Create a simple WHERE condition for subquery
    
    Args:
        table: Table object
        alias: Table alias
    
    Returns:
        ASTNode: WHERE condition node
    """
    try:
        # Select a column
        col = table.get_random_column()
        col_ref = ColumnReferenceNode(col, alias)
        
        # Generate different conditions based on column type
        if col.category == 'numeric':
            # Comparison condition for numeric type column
            operators = ['=', '<', '>', '<=', '>=', '<>']
            operator = random.choice(operators)
            condition = ComparisonNode(operator)
            condition.add_child(col_ref)
            
            # Generate random numeric value
            if col.data_type in ['INT', 'INTEGER', 'BIGINT', 'TINYINT', 'SMALLINT']:
                condition.add_child(LiteralNode(random.randint(0, 100), col.data_type))
            else:
                condition.add_child(LiteralNode(random.uniform(0, 100), col.data_type))
            
        elif col.category == 'string':
            # Comparison condition for string type column
            operators = ['=', '<>', 'LIKE']
            operator = random.choice(operators)
            condition = ComparisonNode(operator)
            condition.add_child(col_ref)
            
            # Generate random string
            if operator == 'LIKE':
                condition.add_child(LiteralNode(f"'sample%'", col.data_type))
            else:
                condition.add_child(LiteralNode(f"'sample_{random.randint(1, 100)}'", col.data_type))
            
        elif col.category == 'datetime':
            # Comparison condition for datetime type column
            operators = ['=', '<', '>', '<=', '>=', '<>']
            operator = random.choice(operators)
            condition = ComparisonNode(operator)
            condition.add_child(col_ref)
            
            # Generate random date
            year = random.randint(2020, 2024)
            month = random.randint(1, 12)
            day = random.randint(1, 28)
            date_str = f'{year}-{month:02d}-{day:02d}'
            condition.add_child(LiteralNode(date_str, col.data_type))
            
        else:
            # Simple equality condition for other types
            condition = ComparisonNode('=')
            condition.add_child(col_ref)
            condition.add_child(LiteralNode(1, 'INT'))
            
        return condition
    except Exception:
        return None

# ------------------------------
# SQL Generation Functions
# ------------------------------

def generate_create_table_sql(table: Table) -> str:
    """Generate CREATE TABLE SQL statement"""
    # Get current database dialect
    dialect = get_current_dialect()
    is_monetdb = bool(dialect and dialect.optimizer_family() == 'monetdb')
    
    parts = [f"CREATE TABLE {table.name} ("]

    # Column definitions
    column_defs = []
    for col in table.columns:
        is_primary_key = col.name == table.primary_key
        # Generate column definition using dialect method
        column_def = dialect.get_column_definition(
            col.name, 
            col.data_type, 
            col.is_nullable, 
            is_primary_key
        )
        column_defs.append(f"    {column_def}")

    # Add primary key constraint if dialect's SERIAL PRIMARY KEY syntax is not used
    if not any("SERIAL" in def_ for def_ in column_defs) and table.primary_key:
        primary_key_def = dialect.get_primary_key_constraint(table.primary_key)
        column_defs.append(f"    {primary_key_def}")

    # Foreign keys - add only if dialect supports them
    if hasattr(dialect, 'supports_foreign_keys') and dialect.supports_foreign_keys():
        for fk in table.foreign_keys:
            column_defs.append(f"    FOREIGN KEY ({fk['column']}) REFERENCES {fk['ref_table']}({fk['ref_column']}) ")

    parts.append(",\n".join(column_defs))
    parts.append(");")

    return "\n".join(parts)


def generate_insert_sql(table: Table, num_rows: int = 5, existing_primary_keys: dict = None, primary_key_values: list = None) -> str:
    """Generate INSERT SQL statements (one INSERT per row)

    Args:
        table: Table object
        num_rows: Number of rows to generate
        existing_primary_keys: Dictionary of primary key values from other tables for foreign key references
    """
    if not table.columns:
        return ""

    # Get current database dialect
    dialect = get_current_dialect()
    is_monetdb = bool(dialect and dialect.optimizer_family() == 'monetdb')
    
    # Column names
    columns = [col.name for col in table.columns]

    # Generate unique values for non-auto-increment primary keys if not provided
    if primary_key_values is None:
        primary_key_values = set()
        for _ in range(num_rows):
            while True:
                val = random.randint(1, 10000)
                if val not in primary_key_values:
                    primary_key_values.add(val)
                    break
        primary_key_values = list(primary_key_values)

    # Generate single INSERT statements
    insert_sqls = []
    for i in range(num_rows):
        row_values = []
        for col in table.columns:
            if col.name == table.primary_key:
                # Use pre-generated unique primary key value
                row_values.append(str(primary_key_values[i]))
            elif any(fk for fk in table.foreign_keys if fk["column"] == col.name):
                # Handle foreign keys, referencing existing primary key values
                fk = next(fk for fk in table.foreign_keys if fk["column"] == col.name)
                ref_table = fk["ref_table"]
                if existing_primary_keys and ref_table in existing_primary_keys and existing_primary_keys[ref_table]:
                    # Randomly select one from the primary key values of the referenced table
                    ref_pk_values = existing_primary_keys[ref_table]
                    if ref_pk_values:
                        selected_pk = random.choice(ref_pk_values)
                        row_values.append(str(selected_pk))
                    else:
                        # Use random value if the referenced table's primary key value list is empty
                        row_values.append(str(random.randint(1, 100)))
                else:
                    # Use random value if there are no primary key values from the referenced table (this may cause foreign key constraint errors in practice)
                    row_values.append(str(random.randint(1, 100)))
            # Determine generation logic based on specific data type
            elif col.data_type.startswith("INT") or col.data_type in ["BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT"]:
                if table.name == "orders" and col.name == "amount":
                    # Use integer for amount column in orders table
                    row_values.append(str(random.randint(10, 1000)))
                else:
                    row_values.append(str(random.randint(0, 100)))
            elif col.data_type.startswith("DECIMAL") or col.data_type.startswith("NUMERIC"):
                row_values.append(f"{random.uniform(10, 1000):.2f}")
            elif col.data_type.startswith("FLOAT") or col.data_type.startswith("DOUBLE"):
                row_values.append(f"{random.uniform(0.0, 100.0):.2f}")
            elif col.data_type.startswith("VARCHAR") or col.data_type in ["CHAR", "TEXT", "LONGTEXT", "MEDIUMTEXT", "TINYTEXT"]:
                # Special handling for status column in orders table
                if table.name == "orders" and col.name == "status":
                    # Randomly select from three statuses
                    status_values = ["finished", "finishing", "to_finish"]
                    row_values.append(f"'{random.choice(status_values)}'")
                # Special handling for email column in users table, generating 10-digit integer@qq.com format
                elif table.name == "users" and col.name == "email":
                    # Generate 10-digit random integer
                    ten_digit_number = random.randint(1000000000, 9999999999)
                    row_values.append(f"'{ten_digit_number}@qq.com'")
                # Special handling for sex column in users table, values are girl or boy
                elif table.name == "users" and col.name == "sex":
                    # Randomly select from two genders
                    sex_values = ["girl", "boy"]
                    row_values.append(f"'{random.choice(sex_values)}'")
                else:
                    # Use pure ASCII characters to generate random strings to avoid UTF-8 encoding issues
                    import string
                    
                    # Determine string length based on data type
                    if col.data_type.startswith("VARCHAR"):
                        # Try to extract length from VARCHAR definition
                        match = re.search(r"VARCHAR\((\d+)\)", col.data_type)
                        if match:
                            max_length = int(match.group(1))
                            # Generate random string not exceeding maximum length
                            string_length = random.randint(1, min(max_length, 255))  # Prevent excessive length
                        else:
                            # Default VARCHAR length
                            string_length = random.randint(5, 20)
                    elif col.data_type.startswith("CHAR"):
                        # Try to extract length from CHAR definition
                        match = re.search(r"CHAR\((\d+)\)", col.data_type)
                        if match:
                            # CHAR type uses fixed length
                            string_length = int(match.group(1))
                        else:
                            # Default CHAR length
                            string_length = 10
                    elif col.data_type == "TINYTEXT":
                        # TINYTEXT maximum length is 255
                        string_length = random.randint(1, 200)
                    elif col.data_type == "TEXT":
                        # TEXT maximum length is 65535
                        string_length = random.randint(1, 500)
                    elif col.data_type == "MEDIUMTEXT":
                        # MEDIUMTEXT maximum length is 16777215
                        string_length = random.randint(1, 1000)
                    elif col.data_type == "LONGTEXT":
                        # LONGTEXT maximum length is 4294967295
                        string_length = random.randint(1, 2000)
                    else:
                        # Default length
                        string_length = random.randint(5, 20)
                    
                    # Generate random string of specified length, ensuring sample_ prefix is included in total length
                    prefix = 'sample_'
                    # Subtract prefix length from total length to ensure total length meets requirements
                    random_part_length = max(1, string_length - len(prefix))  # Ensure at least 1 random character
                    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=random_part_length))
                    row_values.append(f"'{prefix}{random_str}'")
            elif col.data_type == "DATE":
                # Generate random date value
                days = random.randint(0, 365)
                date_val = datetime.now() - timedelta(days=days)
                datetime_literal = dialect.get_datetime_literal(
                    date_val.year, date_val.month, date_val.day
                )
                row_values.append(datetime_literal)
            elif col.data_type == "TIME":
                # Generate random time value
                hours = random.randint(0, 23)
                minutes = random.randint(0, 59)
                seconds = random.randint(0, 59)
                if is_monetdb:
                    row_values.append(f"'{hours:02d}:{minutes:02d}:{seconds:02d}'")
                else:
                    datetime_literal = dialect.get_datetime_literal(
                        2023, 1, 1, hours, minutes, seconds
                    )
                    row_values.append(datetime_literal)
            elif col.data_type in ["DATETIME", "TIMESTAMP"]:
                # Generate random datetime value
                days = random.randint(0, 365)
                hours = random.randint(0, 23)
                minutes = random.randint(0, 59)
                seconds = random.randint(0, 59)
                date_val = datetime.now() - timedelta(days=days)
                datetime_literal = dialect.get_datetime_literal(
                    date_val.year, date_val.month, date_val.day, 
                    hours, minutes, seconds
                )
                row_values.append(datetime_literal)
            elif col.data_type == "BOOLEAN" or col.data_type == "BOOL":
                # Generate boolean representation based on dialect
                is_true = random.choice([True, False])
                if dialect.name == "POSTGRESQL":
                    # PostgreSQL uses TRUE/FALSE keywords
                    row_values.append("TRUE" if is_true else "FALSE")
                else:
                    # MySQL uses 1/0 or true/false strings
                    row_values.append(str(is_true).lower())

            elif col.data_type.startswith("SET"):
                # SET type: randomly select one or more values from allowed values
                # Parse optional values from SET definition
                set_values = re.findall(r"'([^']+)',?", col.data_type)
                if set_values:
                    # Randomly select between 1 and all values
                    num_selected = random.randint(1, len(set_values))
                    selected_values = random.sample(set_values, num_selected)
                    row_values.append("'" + ",".join(selected_values) + "'")
                else:
                    row_values.append("''")
            elif col.data_type.startswith("ENUM"):
                # ENUM type: randomly select one from enum values
                enum_values = re.findall(r"'([^']+)',?", col.data_type)
                if enum_values:
                    row_values.append("'" + random.choice(enum_values) + "'")
                else:
                    row_values.append("NULL")
            elif col.data_type.startswith("BIT"):
                # BIT type: generate random bit value
                # Parse bit count
                bit_count = re.search(r"BIT\((\d+)\)", col.data_type)
                if bit_count:
                    bit_count = int(bit_count.group(1))
                    # Generate random binary number with specified bit count
                    max_value = 2 ** bit_count - 1
                    row_values.append("b'" + bin(random.randint(0, max_value))[2:].zfill(bit_count) + "'")
                else:
                    row_values.append("b'0'")
            elif col.data_type in ["YEAR"]:
                # YEAR type: generate random year between 2000-2023
                row_values.append(str(random.randint(2000, 2023)))
            elif col.data_type in ["GEOMETRY", "POINT", "LINESTRING", "POLYGON"]:
                # GEOMETRY type: generate simple geometric data
                if col.data_type == "POINT":
                    # Generate simple point coordinates
                    lat = round(random.uniform(-90, 90), 6)
                    lng = round(random.uniform(-180, 180), 6)
                    row_values.append(f"ST_GeomFromText('POINT({lat} {lng})')")
                elif col.data_type == "LINESTRING":
                    # Generate simple line
                    points = []
                    for _ in range(2):
                        lat = round(random.uniform(-90, 90), 6)
                        lng = round(random.uniform(-180, 180), 6)
                        points.append(f"{lat} {lng}")
                    row_values.append(f"ST_GeomFromText('LINESTRING({','.join(points)})')")
                elif col.data_type == "POLYGON":
                    # Generate simple polygon
                    points = []
                    for _ in range(4):
                        lat = round(random.uniform(-90, 90), 6)
                        lng = round(random.uniform(-180, 180), 6)
                        points.append(f"{lat} {lng}")
                    # Close the polygon
                    points.append(points[0])
                    row_values.append(f"ST_GeomFromText('POLYGON(({','.join(points)}))')")
                else:
                    # Generic geometry type, default to point
                    lat = round(random.uniform(-90, 90), 6)
                    lng = round(random.uniform(-180, 180), 6)
                    row_values.append(f"ST_GeomFromText('POINT({lat} {lng})')")
            elif col.data_type in ["BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB","BIT(8)"]:
                # BLOB type: generate binary data that meets utf8mb4 requirements (in hexadecimal representation)
                # utf8mb4 encoding rules:
                # 1 byte: 0xxxxxxx
                # 2 bytes: 110xxxxx 10xxxxxx
                # 3 bytes: 1110xxxx 10xxxxxx 10xxxxxx
                # 4 bytes: 11110xxx 10xxxxxx 10xxxxxx 10xxxxxx
                # To ensure compatibility, we generate valid UTF-8 sequences of 1-3 bytes
                utf8_bytes = bytearray()
                total_bytes = random.randint(1, 50)  # Strictly limit total bytes to under 510 to meet aggregate function requirements
                current_size = 0
                
                while current_size < total_bytes:
                    # Randomly choose to generate UTF-8 characters of 1-3 bytes (covers most common characters)
                # Ensure not to exceed total byte limit
                    remaining_bytes = total_bytes - current_size
                    if remaining_bytes >= 3:
                        byte_length = random.choice([1, 2, 3])
                    elif remaining_bytes >= 2:
                        byte_length = random.choice([1, 2])
                    else:
                        byte_length = 1
                    
                    if byte_length == 1:
                        # 1-byte character (ASCII): 0xxxxxxx
                        utf8_bytes.append(random.randint(0x00, 0x7F))
                        current_size += 1
                    elif byte_length == 2:
                        # 2-byte character: 110xxxxx 10xxxxxx
                        utf8_bytes.append(random.randint(0xC0, 0xDF))  # 110xxxxx
                        utf8_bytes.append(random.randint(0x80, 0xBF))  # 10xxxxxx
                        current_size += 2
                    elif byte_length == 3:
                        # 3-byte character: 1110xxxx 10xxxxxx 10xxxxxx
                        utf8_bytes.append(random.randint(0xE0, 0xEF))  # 1110xxxx
                        utf8_bytes.append(random.randint(0x80, 0xBF))  # 10xxxxxx
                        utf8_bytes.append(random.randint(0x80, 0xBF))  # 10xxxxxx
                        current_size += 3
                
                # Convert to hexadecimal string
                hex_data = utf8_bytes.hex().upper()
                row_values.append(f"X'{hex_data}'")
            else:
                # Default to NULL
                row_values.append("NULL")

        # Build single-line INSERT statement
        values_str = ", ".join(row_values)
        insert_sql = f"INSERT INTO {table.name} ({', '.join(columns)}) VALUES ({values_str});"
        insert_sqls.append(insert_sql)

    # Concatenate all INSERT statements
    return "\n".join(insert_sqls)

def create_sample_tables() -> List[Table]:
    """Create sample table structures"""
    current_dialect = get_current_dialect()
    optimizer_family = current_dialect.optimizer_family() if current_dialect else ''
    is_tidb = bool(current_dialect and current_dialect.name == "TIDB")
    is_oceanbase = optimizer_family == 'oceanbase'
    is_monetdb = optimizer_family == 'monetdb'

    # Table t1
    t1 = Table(
        name="t1",
        columns=[
            Column("c1", "INT", "numeric", False, "t1"),
            Column("c2", "VARCHAR(255)", "string", False, "t1"),
            Column("c3", "VARCHAR(255)", "string", True, "t1"),
            Column("c4", "INT", "numeric", True, "t1"),
            Column("c5", "DATE", "datetime", False, "t1"),
            Column("c6", "VARCHAR(10)", "string", False, "t1")
        ],
        primary_key="c1",
        foreign_keys=[]
    )

    # Table t2 - contains more MySQL data types
    t2 = Table(
        name="t2",
        columns=[
            Column("c1", "INT", "numeric", False, "t2"),  # Primary key
            Column("c2", "INT", "numeric", False, "t2"),  # Foreign key, references t1.c1
            Column("c3", "DECIMAL(10,2)", "numeric", False, "t2"),  # Exact decimal type
            Column("c4", "VARCHAR(50)", "string", False, "t2"),  # String type
            Column("c5", "DATE", "datetime", False, "t2"),  # Date type
            Column("c6", "TEXT" if is_monetdb else "MEDIUMTEXT", "string", True, "t2"),  # Medium text type
            Column("c7", "TEXT" if is_monetdb else "LONGTEXT", "string", True, "t2"),  # Long text type
            Column("c8", "VARCHAR(255)" if is_oceanbase else ("BLOB" if is_monetdb else "MEDIUMBLOB"), "string" if is_oceanbase else "binary", True, "t2"),  # OceanBase uses string stand-in to avoid binary decode crashes
            Column("c9", "LONGTEXT" if is_oceanbase else ("BLOB" if is_monetdb else "LONGBLOB"), "string" if is_oceanbase else "binary", True, "t2"),  # OceanBase uses string stand-in to avoid binary decode crashes
            Column("c10", "VARCHAR(32)" if is_monetdb else "ENUM('value1','value2','value3')", "string", True, "t2"),  # Enum type
            Column("c11", "VARCHAR(64)" if is_monetdb else "SET('a','b','c','d')", "string", True, "t2"),  # Set type
            Column("c12", "VARCHAR(8)" if (is_oceanbase or is_monetdb) else "BIT(8)", "string" if (is_oceanbase or is_monetdb) else "binary", True, "t2"),  # MonetDB/OceanBase use string stand-ins for unsupported bit payloads
            Column("c13", "TIMESTAMP" if is_monetdb else "DATETIME", "datetime", True, "t2"),  # Datetime type
            Column("c14", "FLOAT" if is_monetdb else "FLOAT(8,2)", "numeric", True, "t2"),  # Floating point with precision
            Column("c15", "DOUBLE" if is_monetdb else "DOUBLE(12,4)", "numeric", True, "t2")  # Double precision with precision
        ],
        primary_key="c1",
        foreign_keys=[{"column": "c2", "ref_table": "t1", "ref_column": "c1"}]
    )



    # Table t3 - new table, demonstrates more MySQL data types and relationships
    t3 = Table(
        name="t3",
        columns=[
            Column("c1", "INT", "numeric", False, "t3"),  # Primary key
            Column("c2", "INT", "numeric", False, "t3"),  # Foreign key, references t1.c1
            Column("c3", "INT", "numeric", False, "t3"),  # Foreign key, references t2.c1
            Column("c4", "SMALLINT" if is_monetdb else "YEAR", "numeric" if is_monetdb else "datetime", False, "t3"),  # MonetDB uses numeric stand-in for YEAR
            Column("c5", "TIME", "datetime", True, "t3"),  # Time type
            Column("c6", "SMALLINT" if is_monetdb else "TINYINT", "numeric", True, "t3"),  # Tiny integer type
            Column("c7", "SMALLINT", "numeric", True, "t3"),  # Small integer type
            Column("c8", "INT" if is_monetdb else "MEDIUMINT", "numeric", True, "t3"),  # Medium integer type
            Column("c9", "BIGINT", "numeric", True, "t3"),  # Big integer type
            Column("c10", "TEXT" if is_monetdb else "LONGTEXT", "string", True, "t3"),  # Changed JSON to LONGTEXT/TEXT for cross-dialect compatibility
            Column("c11", "VARCHAR(255)" if (is_tidb or is_oceanbase or is_monetdb) else "GEOMETRY", "string" if (is_tidb or is_oceanbase or is_monetdb) else "binary", True, "t3"),  # MonetDB/OceanBase/TiDB use string stand-in to avoid unsupported geometry payloads
            Column("c12", "TEXT" if is_monetdb else "TINYTEXT", "string", True, "t3"),  # Tiny text type
            Column("c13", "TINYTEXT" if is_oceanbase else ("BLOB" if is_monetdb else "TINYBLOB"), "string" if is_oceanbase else "binary", True, "t3"),  # MonetDB/OceanBase use stand-ins to avoid unsupported binary payloads
            Column("c14", "VARCHAR(16)" if is_monetdb else "SET('x','y','z')", "string", True, "t3"),  # Set type
            Column("c15", "SMALLINT" if is_monetdb else "TINYINT(1)", "numeric", True, "t3")  # MonetDB uses numeric stand-in for BOOLEAN-like storage
        ],
        primary_key="c1",
        foreign_keys=[
            {"column": "c2", "ref_table": "t1", "ref_column": "c1"},
            {"column": "c3", "ref_table": "t2", "ref_column": "c1"}
        ]
    )

    return [t1, t2, t3]


def create_sample_functions() -> List[Function]:
    """Create sample function list based on MySQL 8.4 aggregation function specifications"""
    return [
        # Aggregate functions - based on MySQL 8.4 official documentation
        # Basic aggregate functions
        Function("COUNT", 1, 1, ["any"], "INT", "aggregate"),
        Function("COUNT_DISTINCT", 1, 1, ["any"], "INT", "aggregate"),
        # SUM return type: DECIMAL for exact value parameters, DOUBLE for approximate value parameters
        Function("SUM", 1, 1, ["numeric"], "numeric", "aggregate"),
        Function("SUM_DISTINCT", 1, 1, ["numeric"], "numeric", "aggregate"),
        # AVG return type: DECIMAL for exact value parameters, DOUBLE for approximate value parameters
        Function("AVG", 1, 1, ["numeric"], "numeric", "aggregate"),
        # MAX/MIN can accept various types of parameters
        Function("MAX", 1, 1, ["any"], "any", "aggregate"),
        Function("MIN", 1, 1, ["any"], "any", "aggregate"),
        
        # Bitwise aggregate functions - support binary strings and numeric types
        Function("BIT_AND", 1, 1, ["numeric"], "numeric", "aggregate"),
        Function("BIT_OR", 1, 1, ["numeric"], "numeric", "aggregate"),
        Function("BIT_XOR", 1, 1, ["numeric"], "numeric", "aggregate"),
        
        # String aggregate function - GROUP_CONCAT supports multiple parameters and various types
        Function("GROUP_CONCAT", 1, None, ["any"], "string", "aggregate"),
        
        # Statistical analysis aggregate functions - return DOUBLE values for numeric parameters
        Function("STD", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("STDDEV", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("STDDEV_POP", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("STDDEV_SAMP", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("VAR_POP", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("VAR_SAMP", 1, 1, ["numeric"], "DOUBLE", "aggregate"),
        Function("VARIANCE", 1, 1, ["numeric"], "DOUBLE", "aggregate"),

        # Scalar functions
        Function("CONCAT", 2, None, ["string", "string"], "string", "scalar"),
        Function("SUBSTRING", 3, 3, ["string", "numeric", "numeric"], "string", "scalar"),
        Function("ABS", 1, 1, ["numeric"], "numeric", "scalar"),
        Function("ROUND", 2, 2, ["numeric", "numeric"], "numeric", "scalar"),
        # Additional string functions
        Function("LOWER", 1, 1, ["string"], "string", "scalar"),
        Function("UPPER", 1, 1, ["string"], "string", "scalar"),
        Function("LENGTH", 1, 1, ["string"], "INT", "scalar"),
        # Additional mathematical functions
        Function("LOG", 1, 1, ["numeric"], "DOUBLE", "scalar"),
        Function("EXP", 1, 1, ["numeric"], "DOUBLE", "scalar"),
        # Trigonometric functions
        Function("SIN", 1, 1, ["numeric"], "numeric", "scalar"),
        Function("COS", 1, 1, ["numeric"], "numeric", "scalar"),
        Function("TAN", 1, 1, ["numeric"], "numeric", "scalar"),
        # Modified DATE_FORMAT function definition to ensure the second parameter is a format string
            Function("DATE_FORMAT", 2, 2, ["datetime", "string"], "string", "scalar", format_string_required=True),
        # Added TO_CHAR function definition for PostgreSQL dialect
            Function("TO_CHAR", 2, 2, ["datetime", "string"], "string", "scalar", format_string_required=True),
        # Time type-related functions - support mathematical equivalent variations
        
        # Window functions
        Function("ROW_NUMBER", 0, 0, [], "INT", "window"),
        Function("RANK", 0, 0, [], "INT", "window"),
        Function("DENSE_RANK", 0, 0, [], "INT", "window"),
        
    ]


def generate_table_alias() -> str:
    """Generate unique table alias"""
    sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
    # Generate alias using random string to avoid keyword conflicts
    base_alias = ''.join(random.choices(string.ascii_lowercase, k=3))
    # Add random number to ensure uniqueness
    alias = base_alias + str(random.randint(1, 99))
    return alias

def create_join_condition(main_table: Table, main_alias: str, join_table: Union[Table, 'SubqueryNode'], join_alias: str) -> ASTNode:
    """Create join conditions, support multiple advanced join types, ensure type matching"""
    # Handle subquery as join table case
    is_subquery_join = hasattr(join_table, 'column_alias_map')
    
    # Get current database dialect
    from data_structures.db_dialect import DBDialectFactory
    current_dialect = DBDialectFactory.get_current_dialect()
    
    # Check if current dialect supports subqueries in ON conditions
    supports_subqueries_in_join = True
    if hasattr(current_dialect, 'supports_subqueries_in_join_condition'):
        supports_subqueries_in_join = current_dialect.supports_subqueries_in_join_condition()
    
    # Join condition type probability distribution
    condition_types = ['fk', 'simple_eq', 'composite', 'range', 'like', 'null_check', 'in_condition', 'exists_condition', 'expression_based']
    weights = [0.1, 0.1, 0.1, 0.05, 0.1, 0.1, 0.15, 0.2, 0.1]
    
    # For subquery joins, select condition type based on dialect support
    if is_subquery_join:
        if supports_subqueries_in_join:
            condition_type = random.choices(['simple_eq', 'in_condition', 'exists_condition', 'expression_based'], 
                                          weights=[0.3, 0.25, 0.25, 0.2], k=1)[0]
        else:
            # When subquery join conditions are not supported, force simple_eq
            condition_type = 'simple_eq'
    else:
        if not supports_subqueries_in_join:
            # For non-subquery joins, also avoid condition types that contain subqueries
            condition_types_no_subquery = [t for t in condition_types if t not in ['in_condition', 'exists_condition']]
            weights_no_subquery = [weights[i] for i, t in enumerate(condition_types) if t in condition_types_no_subquery]
            # Normalize weights
            total_weight = sum(weights_no_subquery)
            weights_no_subquery = [w / total_weight for w in weights_no_subquery]
            condition_type = random.choices(condition_types_no_subquery, weights=weights_no_subquery, k=1)[0]
        else:
            condition_type = random.choices(condition_types, weights=weights, k=1)[0]
    
    log_message = f"[Join Condition Selection] Table 1: {main_table.name}({main_alias}), Table 2: {'Subquery' if is_subquery_join else join_table.name}({join_alias}), Selected condition type: {condition_type}\n"
    
    
    
    if condition_type == 'fk' and not is_subquery_join:
        # Try to find foreign key relationship
        fk = next((fk for fk in join_table.foreign_keys if fk["ref_table"] == main_table.name), None)
        if fk:
            # Has foreign key relationship, use foreign key join
            # Find actual columns to get correct data types
            fk_column = next((col for col in join_table.columns if col.name == fk["column"]), None)
            ref_column = next((col for col in main_table.columns if col.name == fk["ref_column"]), None)
            
            # Use actual column types instead of hardcoded 'numeric'
            left_col = ColumnReferenceNode(
                fk_column if fk_column else Column(
                    fk["column"], 
                    "", 
                    fk.get("data_type", "INT"), 
                    False, 
                    join_table.name
                ),
                join_alias
            )
            right_col = ColumnReferenceNode(
                ref_column if ref_column else Column(
                    fk["ref_column"], 
                    "", 
                    fk.get("ref_data_type", "INT"), 
                    False, 
                    main_table.name
                ),
                main_alias
            )
            condition = ComparisonNode("=")
            condition.add_child(left_col)
            condition.add_child(right_col)
            return condition
        else:
            # Fall back to simple equality join
            log_message = f"[Fallback] in_condition: Cannot find numeric type column in main table, falling back to simple_eq\n"
            condition_type = 'simple_eq'
    
    if condition_type == 'simple_eq':
        # Simple equality join condition - ensure type matching
        left_col = None
        right_col = None
        
        # Try to find compatible column pairs
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            
            # Select random column from main table
            left_col_candidate = main_table.get_random_column()
            
            # Select appropriate right-side column based on join table type
            if is_subquery_join:
                # Select column from subquery's column alias map
                valid_aliases = list(join_table.column_alias_map.keys())
                selected_alias = random.choice(valid_aliases)
                col_name, data_type, category = join_table.column_alias_map[selected_alias]
                
                # If main table is orders and column is user_id, ensure subquery column is also numeric type
                if main_table.name == 'orders' and left_col_candidate.name == 'user_id':
                    # Filter numeric type subquery columns
                    numeric_aliases = [alias for alias, (_, dt, cat) in join_table.column_alias_map.items() 
                                       if cat == 'numeric' or dt.startswith(('INT', 'BIGINT', 'DECIMAL'))]
                    if numeric_aliases:
                        selected_alias = random.choice(numeric_aliases)
                        col_name, data_type, category = join_table.column_alias_map[selected_alias]
                
                # Create subquery column reference
                right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
            else:
                # Regular table join
                right_col_candidate = join_table.get_random_column()
            if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
                left_col = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                break
            
            
            attempts += 1
        
        # If no compatible column pairs are found, look for the first compatible column pair in both tables
        if left_col is None or right_col is None:
            if is_subquery_join:
                # Subquery join: iterate through each column in the main table, try to find type-compatible subquery columns
                for col1 in main_table.columns:
                    # If main table is orders and column is user_id, prioritize finding numeric type subquery columns
                    if main_table.name == 'orders' and col1.name == 'user_id':
                        numeric_aliases = [alias for alias, (_, dt, cat) in join_table.column_alias_map.items() 
                                          if cat == 'numeric' or dt.startswith(('INT', 'BIGINT', 'DECIMAL'))]
                        if numeric_aliases:
                            selected_alias = random.choice(numeric_aliases)
                            col_name, data_type, category = join_table.column_alias_map[selected_alias]
                            left_col = ColumnReferenceNode(col1, main_alias)
                            right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                            right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                            break
                    
                    # Otherwise try all subquery columns
                    for alias, (col_name, data_type, category) in join_table.column_alias_map.items():
                        if is_type_compatible(col1.data_type, data_type):
                            left_col = ColumnReferenceNode(col1, main_alias)
                            right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                            right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                            break
                    if left_col and right_col:
                        break
            else:
                # Regular table join
                for col1 in main_table.columns:
                    for col2 in join_table.columns:
                        if is_type_compatible(col1.data_type, col2.data_type):
                            left_col = ColumnReferenceNode(col1, main_alias)
                            right_col = ColumnReferenceNode(col2, join_alias)
                            break
                    if left_col and right_col:
                        break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col is None or right_col is None:
                left_col = ColumnReferenceNode(main_table.columns[0], main_alias)
                if is_subquery_join:
                    # Subquery join: select numeric type subquery columns for numeric type main table columns
                    if main_table.columns[0].category == 'numeric':
                        numeric_aliases = [alias for alias, (_, dt, cat) in join_table.column_alias_map.items() 
                                          if cat == 'numeric' or dt.startswith(('INT', 'BIGINT', 'DECIMAL'))]
                        if numeric_aliases:
                            selected_alias = random.choice(numeric_aliases)
                            col_name, data_type, category = join_table.column_alias_map[selected_alias]
                            right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                            right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                        else:
                            # If the subquery has no numeric columns, use numeric literals
                            right_col = create_compatible_literal(main_table.columns[0].data_type)
                    else:
                        # Non-numeric type, select any subquery column
                        valid_aliases = list(join_table.column_alias_map.keys())
                        selected_alias = random.choice(valid_aliases)
                        col_name, data_type, category = join_table.column_alias_map[selected_alias]
                        right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                        right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                else:
                    # For numeric types, use numeric literals instead of column references
                    if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                        right_col = create_compatible_literal(main_table.columns[0].data_type)
                    else:
                        right_col = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        condition = ComparisonNode("=")
        condition.add_child(left_col)
        condition.add_child(right_col)
        return condition
    
    elif condition_type == 'composite':
        # Composite join condition (AND combination)
        composite = LogicalNode("AND")
        
        # First condition (equality join) - ensure type matching
        left_col1 = None
        right_col1 = None
        
        # Try to find compatible column pairs
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            left_col_candidate = main_table.get_random_column()
            right_col_candidate = join_table.get_random_column()
            
            if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
                left_col1 = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col1 = ColumnReferenceNode(right_col_candidate, join_alias)
                break
            
            attempts += 1
        
        # If no compatible column pairs are found, look for the first compatible column pair in both tables
        if left_col1 is None or right_col1 is None:
            for col1 in main_table.columns:
                for col2 in join_table.columns:
                    if is_type_compatible(col1.data_type, col2.data_type):
                        left_col1 = ColumnReferenceNode(col1, main_alias)
                        right_col1 = ColumnReferenceNode(col2, join_alias)
                        break
                if left_col1 and right_col1:
                    break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col1 is None or right_col1 is None:
                left_col1 = ColumnReferenceNode(main_table.columns[0], main_alias)
                # For numeric types, use numeric literals instead of column references
                if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                    right_col1 = create_compatible_literal(main_table.columns[0].data_type)
                else:
                    right_col1 = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        cond1 = ComparisonNode("=")
        cond1.add_child(left_col1)
        cond1.add_child(right_col1)
        composite.add_child(cond1)
        
        # Second condition (possibly other comparison operations) - ensure type matching
        operators = ["<", ">", "<=", ">=", "!="]
        op = random.choice(operators)
        left_col2 = None
        right_col2 = None
        
        attempts = 0
        while attempts < max_attempts:
            left_col_candidate = main_table.get_random_column()
            right_col_candidate = join_table.get_random_column()
            
            if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
                left_col2 = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col2 = ColumnReferenceNode(right_col_candidate, join_alias)
                break
            
            attempts += 1
        
        # If no compatible column pairs are found, look for the first compatible column pair in both tables
        if left_col2 is None or right_col2 is None:
            for col1 in main_table.columns:
                for col2 in join_table.columns:
                    if is_type_compatible(col1.data_type, col2.data_type):
                        left_col2 = ColumnReferenceNode(col1, main_alias)
                        right_col2 = ColumnReferenceNode(col2, join_alias)
                        break
                if left_col2 and right_col2:
                    break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col2 is None or right_col2 is None:
                left_col2 = ColumnReferenceNode(main_table.columns[0], main_alias)
                # For numeric types, use numeric literals instead of column references
                if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                    right_col2 = create_compatible_literal(main_table.columns[0].data_type)
                else:
                    right_col2 = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        cond2 = ComparisonNode(op)
        cond2.add_child(left_col2)
        cond2.add_child(right_col2)
        composite.add_child(cond2)
        return composite
    
    elif condition_type == 'range':
        # Range join condition (using ComparisonNode's BETWEEN operator) - ensure type matching
        left_col = None
        right_col_low = None
        right_col_high = None
        
        # Try to find compatible columns
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            left_col_candidate = main_table.get_random_column()
            
            # Prioritize numeric or date type columns
            if left_col_candidate.category not in ['numeric', 'datetime']:
                attempts += 1
                continue
            
            # Find two right-side columns that are type-compatible with the left-side column
            right_col_low_candidate = None
            right_col_high_candidate = None
            
            # First find a column that is type-compatible with the left-side column
            for _ in range(5):
                candidate = join_table.get_random_column()
                if is_type_compatible(left_col_candidate.data_type, candidate.data_type):
                    right_col_low_candidate = candidate
                    break
            
            if right_col_low_candidate:
                # Find another column that is type-compatible with the left-side column (can be the same column)
                for _ in range(5):
                    candidate = join_table.get_random_column()
                    if is_type_compatible(left_col_candidate.data_type, candidate.data_type):
                        right_col_high_candidate = candidate
                        break
            
            if right_col_low_candidate and right_col_high_candidate:
                left_col = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col_low = ColumnReferenceNode(right_col_low_candidate, join_alias)
                right_col_high = ColumnReferenceNode(right_col_high_candidate, join_alias)
                break
            
            attempts += 1
        
        # If no compatible columns are found, look for the first compatible column pair in both tables
        if left_col is None or right_col_low is None or right_col_high is None:
            # Try to find compatible columns for left_col
            for col1 in main_table.columns:
                for col2 in join_table.columns:
                    if is_type_compatible(col1.data_type, col2.data_type):
                        left_col = ColumnReferenceNode(col1, main_alias)
                        right_col_low = ColumnReferenceNode(col2, join_alias)
                        # Try to find another compatible column or use the same column
                        for col3 in join_table.columns:
                            if is_type_compatible(col1.data_type, col3.data_type):
                                right_col_high = ColumnReferenceNode(col3, join_alias)
                                break
                        if right_col_high is None:
                            right_col_high = right_col_low
                        break
                if left_col and right_col_low and right_col_high:
                    break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col is None or right_col_low is None or right_col_high is None:
                left_col = ColumnReferenceNode(main_table.columns[0], main_alias)
                # For numeric types, use numeric literals instead of column references
                if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                    right_col_low = create_compatible_literal(main_table.columns[0].data_type)
                    right_col_high = create_compatible_literal(main_table.columns[0].data_type)
                else:
                    right_col_low = ColumnReferenceNode(join_table.columns[0], join_alias)
                    right_col_high = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        # Create BETWEEN expression
        between_expr = ComparisonNode("BETWEEN")
        between_expr.add_child(left_col)
        between_expr.add_child(right_col_low)
        between_expr.add_child(right_col_high)
        return between_expr
    
    elif condition_type == 'like':
        # LIKE pattern matching join
        # Select appropriate string columns
        string_col_main = None
        for col in main_table.columns:
            if col.data_type.startswith('VARCHAR') or col.data_type == 'TEXT':
                string_col_main = col
                break
        
        string_col_join = None
        for col in join_table.columns:
            if col.data_type.startswith('VARCHAR') or col.data_type == 'TEXT':
                string_col_join = col
                break
        
        if string_col_main and string_col_join:
            left_col = ColumnReferenceNode(string_col_main, main_alias)
            right_col = ColumnReferenceNode(string_col_join, join_alias)
            condition = ComparisonNode("LIKE")
            condition.add_child(left_col)
            condition.add_child(right_col)
            return condition
        else:
            # Fall back to simple equality join
            condition_type = 'simple_eq'
            left_col = ColumnReferenceNode(
                main_table.get_random_column(),
                main_alias
            )
            right_col = ColumnReferenceNode(
                join_table.get_random_column(),
                join_alias
            )
            condition = ComparisonNode("=")
            condition.add_child(left_col)
            condition.add_child(right_col)
            return condition
    
    elif condition_type == 'null_check':
        # NULL check join condition
        composite = LogicalNode("AND")
        
        # First condition (equality join) - ensure type matching
        left_col1 = None
        right_col1 = None
        
        # Try to find compatible column pairs
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            left_col_candidate = main_table.get_random_column()
            right_col_candidate = join_table.get_random_column()
            
            if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
                left_col1 = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col1 = ColumnReferenceNode(right_col_candidate, join_alias)
                break
            
            attempts += 1
        
        # If no compatible column pairs are found, look for the first compatible column pair in both tables
        if left_col1 is None or right_col1 is None:
            for col1 in main_table.columns:
                for col2 in join_table.columns:
                    if is_type_compatible(col1.data_type, col2.data_type):
                        left_col1 = ColumnReferenceNode(col1, main_alias)
                        right_col1 = ColumnReferenceNode(col2, join_alias)
                        break
                if left_col1 and right_col1:
                    break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col1 is None or right_col1 is None:
                left_col1 = ColumnReferenceNode(main_table.columns[0], main_alias)
                # For numeric types, use numeric literals instead of column references
                if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                    right_col1 = create_compatible_literal(main_table.columns[0].data_type)
                else:
                    right_col1 = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        cond1 = ComparisonNode("=")
        cond1.add_child(left_col1)
        cond1.add_child(right_col1)
        composite.add_child(cond1)
        
        # Second condition (NULL check)
        null_col = ColumnReferenceNode(
            join_table.get_random_column(),
            join_alias
        )
        # Use ComparisonNode's IS NULL/IS NOT NULL operator
        null_op = "IS NOT NULL" if random.random() > 0.5 else "IS NULL"
        cond2 = ComparisonNode(null_op)
        cond2.add_child(null_col)
        composite.add_child(cond2)
          
        return composite
    
    elif condition_type == 'in_condition':
        # IN subquery join condition
        # Prioritize numeric type columns
        numeric_cols_main = [col for col in main_table.columns if col.category == 'numeric']
        if numeric_cols_main:
            selected_col_main = random.choice(numeric_cols_main)
            left_col = ColumnReferenceNode(selected_col_main, main_alias)
            
            # Create IN condition
            in_condition = ComparisonNode('IN')
            in_condition.add_child(left_col)
            
            # Create subquery
            subquery = SelectNode()
            
            # Select table and column for subquery
            if is_subquery_join:
                # For subquery join, use join_table as subquery table
                subquery_table = join_table
                # Generate safe table alias
                sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
                base_alias = 'sub' + str(random.randint(0, 99))
                sub_alias = base_alias if base_alias not in sql_keywords else base_alias + str(random.randint(0, 9))
            else:
                # For regular table join, use join_table
                subquery_table = join_table
                # Generate safe table alias
                sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
                base_alias = subquery_table.name[:3].lower()
                sub_alias = base_alias if base_alias not in sql_keywords else base_alias + str(random.randint(0, 9))
            
            # Create FROM clause
            sub_from = FromNode()
            sub_from.add_table(subquery_table, sub_alias)
            subquery.set_from_clause(sub_from)
            
            # Select type-compatible columns from subquery table
            compatible_cols = [col for col in subquery_table.columns if is_type_compatible(selected_col_main.data_type, col.data_type)]
            if compatible_cols:
                selected_col_sub = random.choice(compatible_cols)
                sub_col_ref = ColumnReferenceNode(selected_col_sub, sub_alias)
                subquery.add_select_expression(sub_col_ref, selected_col_sub.name)
            else:
                # If no compatible columns, use the first numeric type column
                numeric_cols_sub = [col for col in subquery_table.columns if col.category == 'numeric']
                if numeric_cols_sub:
                    selected_col_sub = random.choice(numeric_cols_sub)
                    sub_col_ref = ColumnReferenceNode(selected_col_sub, sub_alias)
                    subquery.add_select_expression(sub_col_ref, selected_col_sub.name)
                else:
                        # Fall back to simple equality join
                        condition_type = 'simple_eq'
                    
            
            # Add WHERE condition to subquery to limit result count
            if selected_col_sub.category == 'numeric':
                where_cond = ComparisonNode('BETWEEN')
                where_cond.add_child(ColumnReferenceNode(selected_col_sub, sub_alias))
                where_cond.add_child(LiteralNode(1, selected_col_sub.data_type))
                where_cond.add_child(LiteralNode(100, selected_col_sub.data_type))
                subquery.set_where_clause(where_cond)
            
            # Add subquery to IN condition
            in_condition.add_child(SubqueryNode(subquery, ''))
            return in_condition
        else:
            # Fall back to simple equality join
            condition_type = 'simple_eq'
    
    elif condition_type == 'exists_condition':
        # EXISTS subquery join condition
        exists_node = ComparisonNode('EXISTS')
        
        # Create correlated subquery
        subquery = SelectNode()
        
        # Select related table
        if is_subquery_join:
            # For subquery join, use join_table as related table
            rel_table = join_table
            # Generate safe table alias
            sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
            base_rel_alias = 'rel' + str(random.randint(0, 99))
            rel_alias = base_rel_alias if base_rel_alias not in sql_keywords else base_rel_alias + str(random.randint(0, 9))
        else:
            # For regular table join, use join_table
            rel_table = join_table
            # Generate safe table alias
            sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
            base_rel_alias = rel_table.name[:3].lower()
            rel_alias = base_rel_alias if base_rel_alias not in sql_keywords else base_rel_alias + str(random.randint(0, 9))
        
        # Create FROM clause
        sub_from = FromNode()
        sub_from.add_table(rel_table, rel_alias)
        subquery.set_from_clause(sub_from)
        
        # Select a column for SELECT
        rel_col = rel_table.get_random_column()
        if rel_col:
            sub_col_ref = ColumnReferenceNode(rel_col, rel_alias)
            subquery.add_select_expression(sub_col_ref, rel_col.name)
        else:
            # Fall back to simple equality join
            condition_type = 'simple_eq'
            
        
        # Create WHERE correlation condition
        where_cond = LogicalNode('AND')
        
        # Find column pairs that can be used for correlation
        max_attempts = 10  # Increase number of attempts
        attempts = 0
        join_condition_created = False
        
        # First try to find type-compatible column pairs (optimization strategy)
        compatible_pairs_found = False
        compatible_pairs = []
        
        # Preprocessing: find all type-compatible column pairs
        if not is_subquery_join:
            for main_col in main_table.columns:
                for rel_col in rel_table.columns:
                    if is_type_compatible(main_col.data_type, rel_col.data_type):
                        compatible_pairs.append((main_col, rel_col))
        
        if compatible_pairs:
            compatible_pairs_found = True
        else:
            pass
        
        while attempts < max_attempts:
            if compatible_pairs_found and attempts < len(compatible_pairs):
                # Use preprocessed compatible column pairs
                main_col_candidate, rel_col_candidate = compatible_pairs[attempts]
            else:
                # Randomly select columns
                main_col_candidate = main_table.get_random_column()
                rel_col_candidate = rel_table.get_random_column()
            
            if is_type_compatible(main_col_candidate.data_type, rel_col_candidate.data_type):
                # Create equality comparison condition
                eq_cond = ComparisonNode('=')
                eq_cond.add_child(ColumnReferenceNode(main_col_candidate, main_alias))
                eq_cond.add_child(ColumnReferenceNode(rel_col_candidate, rel_alias))
                where_cond.add_child(eq_cond)
                join_condition_created = True
                break
            
            attempts += 1
        
        if not join_condition_created:
            # Fall back to simple equality join
            log_message = f"[Fallback] exists_condition: Could not find column pairs for correlation (attempted {attempts} times), falling back to simple_eq\n"
            condition_type = 'simple_eq'
            # Set a flag indicating reprocessing is needed
            needs_retry = True
        else:
            needs_retry = False
        
        if needs_retry:
            # If reprocessing is needed, set condition type to simple_eq
            # The default fallback logic will handle this case later
            pass
        else:
            # Set WHERE clause for subquery
            subquery.set_where_clause(where_cond)
            
            # Add subquery to EXISTS condition
            exists_node.add_child(SubqueryNode(subquery, ''))
            return exists_node
    
    elif condition_type == 'expression_based':
        # Expression-based join (non-equality operator)
        # Select operator
        operators = ['<', '>', '<=', '>=', '!=']
        op = random.choice(operators)
        
        left_col = None
        right_col = None
        
        # Try to find compatible column pairs
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            left_col_candidate = main_table.get_random_column()
            
            if is_subquery_join:
                # Subquery join: select columns from subquery's column alias map
                valid_aliases = list(join_table.column_alias_map.keys())
                selected_alias = random.choice(valid_aliases)
                col_name, data_type, category = join_table.column_alias_map[selected_alias]
                right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
            else:
                # Regular table join
                right_col_candidate = join_table.get_random_column()
            
            if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
                left_col = ColumnReferenceNode(left_col_candidate, main_alias)
                right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                break
            
            attempts += 1
        
        # If no compatible column pairs are found, look for the first compatible column pair in both tables
        if left_col is None or right_col is None:
            if is_subquery_join:
                # Subquery join
                for col1 in main_table.columns:
                    for alias, (col_name, data_type, category) in join_table.column_alias_map.items():
                        if is_type_compatible(col1.data_type, data_type):
                            left_col = ColumnReferenceNode(col1, main_alias)
                            right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                            right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                            break
                    if left_col and right_col:
                        break
            else:
                # Regular table join
                for col1 in main_table.columns:
                    for col2 in join_table.columns:
                        if is_type_compatible(col1.data_type, col2.data_type):
                            left_col = ColumnReferenceNode(col1, main_alias)
                            right_col = ColumnReferenceNode(col2, join_alias)
                            break
                    if left_col and right_col:
                        break
            
            # Only use the first column of the table as a last resort, but try to perform type conversion
            if left_col is None or right_col is None:
                left_col = ColumnReferenceNode(main_table.columns[0], main_alias)
                if is_subquery_join:
                    # Subquery join
                    valid_aliases = list(join_table.column_alias_map.keys())
                    selected_alias = random.choice(valid_aliases)
                    col_name, data_type, category = join_table.column_alias_map[selected_alias]
                    right_col_candidate = Column(col_name, join_table.alias, data_type, False, join_table.alias)
                    right_col = ColumnReferenceNode(right_col_candidate, join_alias)
                else:
                    # For numeric types, use numeric literals instead of column references
                    if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                        right_col = create_compatible_literal(main_table.columns[0].data_type)
                    else:
                        right_col = ColumnReferenceNode(join_table.columns[0], join_alias)
        
        condition = ComparisonNode(op)
        condition.add_child(left_col)
        condition.add_child(right_col)
        return condition
    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write(log_message)
    left_col = None
    right_col = None
    
    # Try to find compatible column pairs
    max_attempts = 10
    attempts = 0
    
    while attempts < max_attempts:
        left_col_candidate = main_table.get_random_column()
        right_col_candidate = join_table.get_random_column()
        
        if is_type_compatible(left_col_candidate.data_type, right_col_candidate.data_type):
            left_col = ColumnReferenceNode(left_col_candidate, main_alias)
            right_col = ColumnReferenceNode(right_col_candidate, join_alias)
            break
        
        attempts += 1
    
    # If no compatible column pairs are found, look for the first compatible column pair in both tables
    if left_col is None or right_col is None:
        for col1 in main_table.columns:
            for col2 in join_table.columns:
                if is_type_compatible(col1.data_type, col2.data_type):
                    left_col = ColumnReferenceNode(col1, main_alias)
                    right_col = ColumnReferenceNode(col2, join_alias)
                    break
            if left_col and right_col:
                break
        
        # Only use the first column of the table as a last resort, but try to perform type conversion
        if left_col is None or right_col is None:
            left_col = ColumnReferenceNode(main_table.columns[0], main_alias)
            # For numeric types, use numeric literals instead of column references
            if main_table.columns[0].category == 'numeric' and join_table.columns[0].category != 'numeric':
                right_col = create_compatible_literal(main_table.columns[0].data_type)
            else:
                right_col = ColumnReferenceNode(join_table.columns[0], join_alias)
    
    condition = ComparisonNode("=")
    condition.add_child(left_col)
    condition.add_child(right_col)
    
    # Join condition creation completed
    
    return condition


def is_type_compatible(type1, type2):
    """Check if two data types are compatible, allowing conversion between numeric types
    
    Parameters:
    - type1: First data type
    - type2: Second data type
    
    Returns:
    - bool: True if the two types are compatible, False otherwise
    """
    # Use the category attribute of columns to determine type compatibility
    # Note: When type1 and type2 are Column objects, directly use their category attribute
    # When type1 and type2 are strings, they represent data type names, and we need to find corresponding columns from table structures
    # However, since this function is usually used in contexts where Column objects already exist, we prioritize using the category attribute
    
    # In actual projects, column information should be looked up from the TABLES global variable
    # But for backward compatibility, we retain the judgment logic based on data type strings
    
    # Define type compatibility rules
    numeric_types = {'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}
    string_types = {'VARCHAR', 'CHAR', 'TEXT', 'LONGTEXT', 'MEDIUMTEXT', 'TINYTEXT'}
    datetime_types = {'DATE', 'DATETIME', 'TIMESTAMP', 'TIME'}
    
    # Extract base type (remove parentheses and length information)
    base_type1 = type1.split('(')[0].upper() if type1 else 'UNKNOWN'
    base_type2 = type2.split('(')[0].upper() if type2 else 'UNKNOWN'
    
    # Relax type matching rules
    # 1. Types from the same type group are considered compatible
    if (base_type1 in numeric_types and base_type2 in numeric_types) or \
       (base_type1 in string_types and base_type2 in string_types) or \
       (base_type1 in datetime_types and base_type2 in datetime_types):
        return True
    
    # 2. Exactly the same types are considered compatible
    if base_type1 == base_type2:
        return True
    
    return False

def create_compatible_literal(data_type):
    """Create compatible type literals, ensuring type matching and correct quote handling"""
    base_type = data_type.split('(')[0].upper()
    
    if base_type in {'INT', 'BIGINT', 'SMALLINT', 'TINYINT'}:
        # Integer type: return random integer value
        return LiteralNode(random.randint(1, 1000), data_type)
    elif base_type in {'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}:
        # Numeric type: return random floating-point value
        return LiteralNode(round(random.uniform(1.0, 100.0), 2), data_type)
    elif base_type in {'VARCHAR', 'CHAR', 'TEXT', 'STRING'}:
        # String type: return plain text string, explicitly use 'STRING' type to ensure quotes are added correctly
        return LiteralNode(f"sample_{random.randint(1, 100)}", 'STRING')
    elif base_type == 'DATE':
        # Date type: return string in date format
        return LiteralNode(f"2023-01-01", data_type)
    elif base_type in {'DATETIME', 'TIMESTAMP','timestamp'}:
        # Datetime type: return string in datetime format
        return LiteralNode(f"2023-01-01 12:00:00", data_type)
    elif base_type == 'BOOLEAN':
        # Boolean type: return boolean value
        return LiteralNode(random.choice([True, False]), data_type)

    elif base_type == 'SET':
        # SET type: randomly select one from allowed values
        set_values = re.findall(r"'([^']+)',?", data_type)
        if set_values:
            return LiteralNode("'" + random.choice(set_values) + "'", data_type)
        else:
            return LiteralNode("''", data_type)
    elif base_type == 'ENUM':
        # ENUM type: randomly select one from enum values
        enum_values = re.findall(r"'([^']+)',?", data_type)
        if enum_values:
            return LiteralNode("'" + random.choice(enum_values) + "'", data_type)
        else:
            return LiteralNode("NULL", data_type)
    elif base_type == 'BIT':
        # BIT type: generate random bit value
        bit_count = re.search(r"BIT\((\d+)\)", data_type)
        if bit_count:
            bit_count = int(bit_count.group(1))
            max_value = 2 ** bit_count - 1
            return LiteralNode("b'" + bin(random.randint(0, max_value))[2:].zfill(bit_count) + "'", data_type)
        else:
            return LiteralNode("b'0'", data_type)
    elif base_type == 'YEAR':
        # YEAR type: generate random year between 2000-2023
        return LiteralNode(str(random.randint(2000, 2023)), data_type)
    elif base_type in {'GEOMETRY', 'POINT'}:
        # Geometry type: generate simple point coordinates
        lat = round(random.uniform(-90, 90), 6)
        lng = round(random.uniform(-180, 180), 6)
        return LiteralNode(f"ST_GeomFromText('POINT({lat} {lng})')", data_type)
    elif base_type in ['BLOB', 'TINYBLOB', 'MEDIUMBLOB', 'LONGBLOB']:
        # BLOB type: generate random binary data (represented in hexadecimal)
        # To avoid invalid utf8mb4 character errors in MariaDB, use CONVERT function to ensure correct handling
        blob_size = random.randint(1, 510)  # Limit to 510 bytes to meet aggregate function requirements
        hex_data = ''.join(random.choice('0123456789ABCDEF') for _ in range(blob_size * 2))
        return LiteralNode(f"X'{hex_data}", data_type)
    else:
        # Default case: return integer
        return LiteralNode(random.randint(1, 100), 'INT')

def ensure_boolean_expression(expr: ASTNode, tables: List[Table], functions: List[Function], 
                           from_node: FromNode, main_table: Table, main_alias: str, 
                           join_table: Optional[Table] = None, join_alias: Optional[str] = None) -> ASTNode:
    """Ensure the expression is boolean type, convert to boolean expression if not"""
    # If the expression itself is a comparison expression or logical expression, consider it boolean type
    if isinstance(expr, (ComparisonNode, LogicalNode)):
        return expr
    
    # For other types of expressions (such as column references, function calls, etc.), convert them to boolean expressions
    # Select table and column
    
    if isinstance(expr, FunctionCallNode):
        # Use the passed expression as the comparison column, instead of randomly getting it from the table
        col = expr
        return_type = expr.metadata.get('return_type', '').upper()
        
        # For functions with return type 'any' (such as max, min), get the type of the first parameter
        if return_type == 'ANY' and expr.children:
            # All necessary classes have been imported at the top of the file, no need for local import
            
            first_param = expr.children[0]
            param_type = None
            
            if isinstance(first_param, ColumnReferenceNode):
                # If the first parameter is a column reference, directly use the column's type
                param_type = first_param.column.category
                if param_type in ['int', 'float', 'decimal']:
                    param_type = 'numeric'
            elif isinstance(first_param, LiteralNode):
                # If the first parameter is a literal, judge based on data type
                if hasattr(first_param, 'data_type'):
                    data_type = first_param.data_type.upper()
                    if data_type in ['INT', 'FLOAT', 'DECIMAL', 'NUMERIC', 'BIGINT', 'SMALLINT', 'TINYINT']:
                        param_type = 'numeric'
                    elif data_type in ['VARCHAR', 'STRING', 'CHAR', 'TEXT']:
                        param_type = 'string'
                    elif data_type in ['DATE', 'DATETIME', 'TIMESTAMP', 'TIME']:
                        param_type = 'datetime'
            elif isinstance(first_param, FunctionCallNode):
                # If the first parameter is a function call, use its return type
                param_func_return_type = first_param.metadata.get('return_type', '').upper()
                if param_func_return_type in {'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}:
                    param_type = 'numeric'
                elif param_func_return_type in {'VARCHAR', 'CHAR', 'TEXT', 'LONGTEXT', 'MEDIUMTEXT', 'TINYTEXT'}:
                    param_type = 'string'
                elif param_func_return_type in {'DATE', 'DATETIME', 'TIMESTAMP', 'TIME'}:
                    param_type = 'datetime'
            
            if param_type:
                # Set the actual return type of the function call node
                col.metadata['return_type'] = param_type
                col.category = param_type
        else:
            # Handle functions with explicit return types
            if return_type in {'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}:
                col.category = 'numeric'
            elif return_type in {'VARCHAR', 'CHAR', 'TEXT', 'LONGTEXT', 'MEDIUMTEXT', 'TINYTEXT', 'STRING'}:
                col.category = 'string'
            elif return_type in {'DATE', 'DATETIME', 'TIMESTAMP', 'TIME'}:
                col.category = 'datetime'
            else:
                pass
        
        
    elif type(expr).__name__ == 'ColumnReferenceNode':
        # Use the passed column reference as the comparison column
        col = expr
        col.category = expr.column.category
        col.data_type = expr.column.data_type

    elif type(expr).__name__ == 'SubqueryNode':
        # For subqueries, assume it returns a single column and set appropriate type information
        col = expr
        # Default to string type assumption
        col.category = 'string'
        col.data_type = 'VARCHAR'
        # Try to get actual type information from the subquery's column_alias_map
        if hasattr(expr, 'column_alias_map') and expr.column_alias_map:
            # Get type information of the first column
            first_col_info = next(iter(expr.column_alias_map.values()))
            if len(first_col_info) >= 3:
                col.data_type = first_col_info[1]  # Data type
                col.category = first_col_info[2]   # Category
    
    elif type(expr).__name__ == 'LiteralNode':
        col=expr
        col.category = 'numeric'
    else:
        # For expressions of other types, use default type
        col = expr
        # Default assumption is string type
        col.category = 'string'
        col.data_type = 'VARCHAR'
    # Choose operator based on column type
    if col.category == 'string':
        operator = random.choice(['=', '<>'])
    else:
        operator = random.choice(['=', '<>', '<', '>'])
    
    comp_node = ComparisonNode(operator)
    comp_node.add_child(expr)

    # Add right operand (ensure type compatibility)
    if col.category == 'numeric':
        value = random.randint(0, 100)
        comp_node.add_child(LiteralNode(value, col.data_type))
    elif col.category == 'string':
        value = f"sample_{random.randint(1, 100)}"
        # Explicitly use 'STRING' type to ensure proper string quoting
        comp_node.add_child(LiteralNode(value, 'STRING'))
    elif col.category == 'datetime':
        # Generate datetime constant with time component
        year = 2023
        month = random.randint(1, 12)
        day = random.randint(1, 28)  # Simple handling to avoid month-end issues
        hour = random.randint(0, 23)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        
        # Build complete datetime string
        value = f"'{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'"
        
        # Use explicit DATETIME type to ensure quotes are added
        comp_node.add_child(LiteralNode(value, 'DATETIME'))
    elif col.category == 'binary':
        # Generate random hexadecimal value
        hex_value = ''.join(random.choices('0123456789ABCDEF', k=8))
        comp_node.add_child(LiteralNode(f"X'{hex_value}'", 'BINARY'))
        
    return comp_node



def create_complex_expression(tables: List[Table], functions: List[Function],
                           from_node: FromNode, main_table: Table, main_alias: str,
                           join_table: Optional[Table] = None, join_alias: Optional[str] = None,
                           max_depth: int = 3, depth: int = 0, column_tracker: Optional[ColumnUsageTracker] = None, for_select: bool = False) -> ASTNode:
    """Create complex expression nesting, ensuring the generated logical expressions contain only boolean-type sub-expressions"""
    
    # Avoid excessive expression nesting
    if depth >= max_depth:
        expr = create_random_expression(tables, functions, from_node, main_table, main_alias, 
                                       join_table, join_alias, use_subquery=True, column_tracker=column_tracker, for_select=for_select)
        # Ensure boolean expression is returned
        return ensure_boolean_expression(expr, tables, functions, from_node, main_table, main_alias, 
                                       join_table, join_alias)
        
    # Recursively create nested expressions
    if random.random() < 0.4:
        # Create logical expression combining multiple simple expressions
        operator = random.choice(['AND', 'OR'])
        logic_node = LogicalNode(operator)
        
        # Add 2-3 sub-expressions
        sub_expr_count = random.randint(2, 3)
        
        for i in range(sub_expr_count):
            # Recursively create sub-expressions
            sub_expr = create_complex_expression(tables, functions, from_node, main_table, main_alias, 
                                                join_table, join_alias, depth + 1, max_depth, column_tracker=column_tracker, for_select=False)
            # Ensure sub-expressions are boolean type
            logic_node.add_child(sub_expr)
        
        return logic_node
    else:
        # Create base expression
        expr = create_random_expression(tables, functions, from_node, main_table, main_alias, 
                                       join_table, join_alias, use_subquery=True, column_tracker=column_tracker, for_select=for_select)
        # Ensure returns a boolean expression
        result = ensure_boolean_expression(expr, tables, functions, from_node, main_table, main_alias, 
                                       join_table, join_alias)
        return result

def create_random_expression(tables: List[Table], functions: List[Function], 
                           from_node: FromNode, main_table: Table, main_alias: str, 
                           join_table: Optional[Table] = None, join_alias: Optional[str] = None, 
                           use_subquery: bool = True, column_tracker: ColumnUsageTracker = None, for_select: bool=False) -> ASTNode:
    """Create random expression"""
    if random.random() > 0.3 and functions:  # 70% probability to use columns, 30% probability to use functions
        func = random.choice(functions)
        func_node = FunctionCallNode(func)
        
        # Special handling for aggregate functions like MIN/MAX to ensure return type matches parameter type
        if func.name in ['MIN', 'MAX'] and func.return_type == 'any':
            # Preset as numeric, will be updated based on parameter type later
            func_node.metadata['category'] = 'numeric'
        else:
            # Set type information for function expression
            if func.return_type in ['numeric', 'string', 'datetime']:
                func_node.metadata['category'] = func.return_type
            else:
                func_node.metadata['category'] = 'numeric'  # Default to numeric type
        
        # Set necessary window clause information for window functions
        if func.func_type == 'window':
            # Randomly choose whether to add PARTITION BY clause
            if random.random() > 0.3:
                # Choose a table for partitioning
                tables_to_choose = [main_table] + ([join_table] if join_table else [])
                if tables_to_choose:
                    table = random.choice(tables_to_choose)
                    alias = main_alias if table == main_table else join_alias
                    # Choose a column for partitioning
                    col = table.get_random_column()
                    func_node.metadata['partition_by'] = [f"{alias}.{col.name}"]
            
            # Always add ORDER BY clause for window functions that require it
            if func.name in ['ROW_NUMBER', 'RANK', 'DENSE_RANK', 'NTILE', 'LEAD', 'LAG']:
                # Choose a table for sorting
                tables_to_choose = [main_table] + ([join_table] if join_table else [])
                if tables_to_choose:
                    table = random.choice(tables_to_choose)
                    alias = main_alias if table == main_table else join_alias
                    # Choose a column for sorting
                    col = table.get_random_column()
                    # Randomly choose sort direction
                    direction = 'ASC' if random.random() > 0.5 else 'DESC'
                    func_node.metadata['order_by'] = [f"{alias}.{col.name} {direction}"]
                else:
                    # Fallback solution, use constant expression for sorting
                    func_node.metadata['order_by'] = ['1=1']
        
        # Add parameters to function
        param_count = func.min_params
        if func.max_params is not None and func.max_params > func.min_params:
            param_count = random.randint(func.min_params, func.max_params)
        else:
            param_count = func.min_params
        
        # Save the type of the first parameter for aggregate functions like MIN/MAX
        first_param_category = None
        
        for param_idx in range(param_count):
            expected_type = func.param_types[param_idx] if param_idx < len(func.param_types) else 'any'
            
            # Unified handling of special logic for three functions
            # 1. Special handling for SUBSTRING function: Ensure parameter types are correct
            if func.name == 'SUBSTRING':
                # First parameter must be string type
                if param_idx == 0:
                    # Force first parameter to be string type
                    tables_to_choose = [main_table] + ([join_table] if join_table else [])
                    if tables_to_choose:
                        # Try to find a string type column
                        all_string_cols = []
                        for table in tables_to_choose:
                            string_cols = [col for col in table.columns if col.category == 'string']
                            all_string_cols.extend(string_cols)
                        
                        if all_string_cols:
                            # Directly select available column
                            col = random.choice(all_string_cols)
                            table = [t for t in tables_to_choose if col in t.columns][0]
                            alias = main_alias if table == main_table else join_alias
                            
                            # Record column used in select
                            if column_tracker:
                                column_tracker.mark_column_as_select(alias, col.name)
                            
                            col_ref = ColumnReferenceNode(col, alias)
                            func_node.add_child(col_ref)
                        else:
                            # No available string columns, use string literal
                            literal = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                            func_node.add_child(literal)
                    else:
                        # No available tables, use string literal
                        literal = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                        func_node.add_child(literal)
                # Second and third parameters must be numeric (position and length)
                elif param_idx in [1, 2]:
                    # Generate a reasonable integer value
                    # For position parameter, use random number between 1 and 20
                    # For length parameter, use random number between 1 and 10
                    value = random.randint(1, 20) if param_idx == 1 else random.randint(1, 10)
                    literal = LiteralNode(value, 'INT')
                    func_node.add_child(literal)

            # 2. Special handling for DATE_FORMAT/TO_CHAR functions
            elif (func.name == 'DATE_FORMAT' or func.name == 'TO_CHAR'):
                # First parameter: datetime column
                if param_idx == 0:
                    # Force first parameter to be datetime type
                    tables_to_choose = [main_table] + ([join_table] if join_table else [])
                    if tables_to_choose:
                        # Try to find datetime type columns
                        all_datetime_cols = []
                        for table in tables_to_choose:
                            datetime_cols = [col for col in table.columns if col.category == 'datetime']
                            all_datetime_cols.extend(datetime_cols)
                        
                        if all_datetime_cols:
                            # Directly select available column
                            col = random.choice(all_datetime_cols)
                            table = [t for t in tables_to_choose if col in t.columns][0]
                            alias = main_alias if table == main_table else join_alias
                            # Record column usage in select
                            if column_tracker:
                                column_tracker.mark_column_as_select(alias, col.name)
                            col_ref = ColumnReferenceNode(col, alias)
                            func_node.add_child(col_ref)
                        else:
                            # No datetime columns, use date literal
                            literal = LiteralNode('2023-01-01', 'DATE')
                            func_node.add_child(literal)
                    else:
                        # No available tables, use date literal
                        literal = LiteralNode('2023-01-01', 'DATE')
                        func_node.add_child(literal)
                # Second parameter: format string literal
                elif param_idx == 1:
                    # Second parameter must be a format string
                    if func.name == 'TO_CHAR':
                        # PostgreSQL TO_CHAR format
                        format_strings = ['YYYY-MM-DD', 'YYYY-MM-DD HH24:MI:SS', 'DD-MON-YYYY', 'HH24:MI:SS']
                    else:
                        # MySQL DATE_FORMAT format
                        format_strings = ['%Y-%m-%d', '%Y-%m-%d %H:%i:%s', '%d-%b-%Y', '%H:%i:%s']
                    # Use STRING type to ensure quotes are added correctly
                    literal = LiteralNode(random.choice(format_strings), 'STRING')
                    func_node.add_child(literal)

            # 3. Special handling for CONCAT function: ensure all parameters are string type
            elif func.name == 'CONCAT' and expected_type == 'string':
                # Force parameter to be string type
                tables_to_choose = [main_table] + ([join_table] if join_table else [])
                if tables_to_choose:
                    # Try to find string type columns
                    all_string_cols = []
                    for table in tables_to_choose:
                        string_cols = [col for col in table.columns if col.category == 'string']
                        all_string_cols.extend(string_cols)
                    
                    if all_string_cols:
                        # Directly select available column
                        col = random.choice(all_string_cols)
                        table = [t for t in tables_to_choose if col in t.columns][0]
                        alias = main_alias if table == main_table else join_alias
                        # Record column used in select
                        if column_tracker:
                            column_tracker.mark_column_as_select(alias, col.name)
                        col_ref = ColumnReferenceNode(col, alias)
                        func_node.add_child(col_ref)
                    else:
                        # No available string columns, use string literal
                        literal = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                        func_node.add_child(literal)
                else:
                    # No available tables, use string literal
                    literal = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                    func_node.add_child(literal)

            # Special handling for DATE/YEAR/MONTH/DAY/DATEDIFF functions: parameters should be datetime type
            elif func.name in ['DATE', 'YEAR', 'MONTH', 'DAY', 'DATEDIFF'] and expected_type == 'datetime':
                # Force parameter to be datetime type
                tables_to_choose = [main_table] + ([join_table] if join_table else [])
                if tables_to_choose:
                    # Try to find datetime type columns
                    all_datetime_cols = []
                    for table in tables_to_choose:
                        datetime_cols = [col for col in table.columns if col.category == 'datetime']
                        all_datetime_cols.extend(datetime_cols)
                    
                    if all_datetime_cols:
                            # Directly select available column
                            col = random.choice(all_datetime_cols)
                            table = [t for t in tables_to_choose if col in t.columns][0]
                            alias = main_alias if table == main_table else join_alias
                            # Record column usage in select
                            if column_tracker:
                                column_tracker.mark_column_as_select(alias, col.name)
                            col_ref = ColumnReferenceNode(col, alias)
                            func_node.add_child(col_ref)
                    else:
                        # No datetime columns, use date literal
                        literal = LiteralNode('2023-01-01', 'DATE')
                        func_node.add_child(literal)
                else:
                    # No available tables, use date literal
                    literal = LiteralNode('2023-01-01', 'DATE')
                    func_node.add_child(literal)
            else:
                # Regular parameter handling
                # Select table and column
                tables_to_choose = [main_table] + ([join_table] if join_table else [])
                if tables_to_choose:
                    table = random.choice(tables_to_choose)
                    alias = main_alias if table == main_table else join_alias
                    
                        # Directly select available column
                    # Select column of matching type
                    if expected_type == 'any' or not tables_to_choose:
                        col = table.get_random_column()
                    else:
                        matching_columns = [col for col in table.columns if col.category == expected_type]
                        if matching_columns:
                            col = random.choice(matching_columns)
                        else:
                            col = table.get_random_column()
                    # Record column used in select
                    if column_tracker:
                        column_tracker.mark_column_as_select(alias, col.name)
                    col_ref = ColumnReferenceNode(col, alias)
                    func_node.add_child(col_ref)
                    
                    # Record type of first parameter
                    if param_idx == 0 and func.name in ['MIN', 'MAX'] and func.return_type == 'any':
                        first_param_category = col.category
                else:
                    # No available tables, use literal
                    if expected_type == 'numeric':
                        literal = LiteralNode(random.randint(1, 100), 'INT')
                    elif expected_type == 'string':
                        literal = LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
                    else:
                        literal = LiteralNode(random.randint(1, 100), 'INT')
                    func_node.add_child(literal)
                    
                    # Record type of first parameter (if literal)
                    if param_idx == 0 and func.name in ['MIN', 'MAX'] and func.return_type == 'any':
                        if expected_type == 'numeric':
                            first_param_category = 'numeric'
                        elif expected_type == 'string':
                            first_param_category = 'string'
                        else:
                            first_param_category = 'numeric'
        
        # For MIN/MAX functions, update return type based on first parameter type
        if func.name in ['MIN', 'MAX'] and func.return_type == 'any' and first_param_category:
            func_node.metadata['category'] = first_param_category
        
        return func_node
    elif random.random() >0.5:
        return create_select_subquery(tables,functions)
    else:
        # Use simple column reference
        tables_to_choose = [main_table] + ([join_table] if join_table else [])
        if tables_to_choose:
            table = random.choice(tables_to_choose)
            alias = main_alias if table == main_table else join_alias
            # Use column tracker to select columns not used in select, having, and on
            col = get_random_column_with_tracker(table, alias, column_tracker, for_select)
            col_ref = ColumnReferenceNode(col, alias)
            # Set type information for column reference
            col_ref.metadata['category'] = col.category
            return col_ref
        else:
            # Fallback solution, use literal
            literal = LiteralNode(random.randint(1, 100), 'INT')
            # Set type information for literal
            literal.metadata['category'] = 'numeric'
            return literal


def create_expression_of_type(expr_type: str, tables: List[Table], functions: List[Function], 
                             from_node: FromNode, main_table: Table, main_alias: str, 
                             join_table: Optional[Table] = None, join_alias: Optional[str] = None, 
                             column_tracker: Optional[ColumnUsageTracker] = None) -> ASTNode:
    """Create specific type of expression"""
    # Prioritize matching type columns
    tables_to_choose = [main_table] + ([join_table] if join_table else [])
    for table in tables_to_choose:
        matching_columns = [col for col in table.columns if col.category == expr_type]
        if matching_columns:
            alias = main_alias if table == main_table else join_alias
            col = random.choice(matching_columns)
            # Mark column as used in select
            if column_tracker:
                column_tracker.mark_column_as_select(alias, col.name)
            col_ref = ColumnReferenceNode(col, alias)
            return col_ref
    
    # If no matching type column exists, create a function expression of matching type
    if expr_type == 'numeric':
        # Find functions that return numeric type
        numeric_funcs = [f for f in functions if f.return_type == 'numeric' or f.return_type == 'any']
        if numeric_funcs:
            func = random.choice(numeric_funcs)
            func_node = FunctionCallNode(func)
            
            # Add sufficient number of parameters
            # Special handling for three functions
            if func.name == 'SUBSTRING':
                # SUBSTRING function special handling: first parameter is string type, second and third are numeric types
                # First parameter: string type
                string_columns = []
                for table in tables_to_choose:
                    string_columns.extend([col for col in table.columns if col.category == 'string'])
                
                if string_columns:
                    col = random.choice(string_columns)
                    table = [t for t in tables_to_choose if col in t.columns][0]
                    alias = main_alias if table == main_table else join_alias
                    # Mark column as used in select
                    if column_tracker:
                        column_tracker.mark_column_as_select(alias, col.name)
                    col_ref = ColumnReferenceNode(col, alias)
                else:
                    # No string type columns, use literal string
                    col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                func_node.add_child(col_ref)
                
                # Second and third parameters: numeric type
                for i in range(1, func.min_params):
                    # Position parameter (1-20) and length parameter (1-10)
                    value = random.randint(1, 20) if i == 1 else random.randint(1, 10)
                    literal = LiteralNode(value, 'INT')
                    func_node.add_child(literal)
            elif func.name in ['DATE_FORMAT', 'TO_CHAR']:
                # DATE_FORMAT/TO_CHAR function special handling: first parameter is datetime type, second is format string
                # First parameter: datetime type
                datetime_columns = []
                for table in tables_to_choose:
                    datetime_columns.extend([col for col in table.columns if col.category == 'datetime'])
                
                if datetime_columns:
                    col = random.choice(datetime_columns)
                    table = [t for t in tables_to_choose if col in t.columns][0]
                    alias = main_alias if table == main_table else join_alias
                    # Mark column as used in select
                    if column_tracker:
                        column_tracker.mark_column_as_select(alias, col.name)
                    col_ref = ColumnReferenceNode(col, alias)
                else:
                    # No datetime type columns, use date literal
                    col_ref = LiteralNode('2023-01-01', 'DATE')
                func_node.add_child(col_ref)
                
                # Second parameter: format string
                if func.min_params >= 2:
                    if func.name == 'TO_CHAR':
                        # PostgreSQL TO_CHAR format
                        format_strings = ['YYYY-MM-DD', 'YYYY-MM-DD HH24:MI:SS', 'DD-MON-YYYY', 'HH24:MI:SS']
                    else:
                        # MySQL DATE_FORMAT format
                        format_strings = ['%Y-%m-%d', '%Y-%m-%d %H:%i:%s', '%d-%b-%Y', '%H:%i:%s']
                    literal = LiteralNode(random.choice(format_strings), 'STRING')
                    func_node.add_child(literal)
            elif func.name == 'CONCAT':
                # CONCAT function special handling: ensure all parameters are string type
                for param_idx in range(func.min_params):
                    # Try to find string type columns
                    string_columns = []
                    for table in tables_to_choose:
                        string_columns.extend([col for col in table.columns if col.category == 'string'])
                    
                    if string_columns:
                        col = random.choice(string_columns)
                        table = [t for t in tables_to_choose if col in t.columns][0]
                        alias = main_alias if table == main_table else join_alias
                        # Mark column as used in select
                        if column_tracker:
                            column_tracker.mark_column_as_select(alias, col.name)
                        col_ref = ColumnReferenceNode(col, alias)
                    else:
                        # No string type columns, use literal string
                        col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                    func_node.add_child(col_ref)
            else:
                # Use general logic for other functions
                for param_idx in range(func.min_params):
                    table = random.choice(tables_to_choose)
                    alias = main_alias if table == main_table else join_alias
                    # Use column tracker to select columns not used in select, having, and on
                    col = get_random_column_with_tracker(table, alias, column_tracker, for_select=True)
                    # Mark column as used in select
                    if column_tracker:
                        column_tracker.mark_column_as_select(alias, col.name)
                    col_ref = ColumnReferenceNode(col, alias)
                    func_node.add_child(col_ref)
            
            return func_node
    elif expr_type == 'string':
        # Find functions that return string type
        string_funcs = [f for f in functions if f.return_type == 'string']
        if string_funcs:
            func = random.choice(string_funcs)
            func_node = FunctionCallNode(func)
            
            # Add sufficient number of parameters
            # Special handling for three functions
            if func.name == 'SUBSTRING':
                # SUBSTRING function special handling: first parameter is string type, second and third are numeric types
                # First parameter: string type
                string_columns = []
                for table in tables_to_choose:
                    string_columns.extend([col for col in table.columns if col.category == 'string'])
                
                if string_columns:
                    col = random.choice(string_columns)
                    table = [t for t in tables_to_choose if col in t.columns][0]
                    alias = main_alias if table == main_table else join_alias
                    col_ref = ColumnReferenceNode(col, alias)
                else:
                    # No string type columns, use literal string
                    col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                func_node.add_child(col_ref)
                
                # Second and third parameters: numeric types
                for i in range(1, func.min_params):
                    # Position parameter (1-20) and length parameter (1-10)
                    value = random.randint(1, 20) if i == 1 else random.randint(1, 10)
                    literal = LiteralNode(value, 'INT')
                    func_node.add_child(literal)
            elif func.name in ['DATE_FORMAT', 'TO_CHAR']:
                # DATE_FORMAT/TO_CHAR function special handling: first parameter is datetime type, second is format string
                # First parameter: datetime type
                datetime_columns = []
                for table in tables_to_choose:
                    datetime_columns.extend([col for col in table.columns if col.category == 'datetime'])
                
                if datetime_columns:
                    col = random.choice(datetime_columns)
                    table = [t for t in tables_to_choose if col in t.columns][0]
                    alias = main_alias if table == main_table else join_alias
                    col_ref = ColumnReferenceNode(col, alias)
                else:
                    # No datetime type columns, use date literal
                    col_ref = LiteralNode('2023-01-01', 'DATE')
                func_node.add_child(col_ref)
                
                # Second parameter: format string
                if func.min_params >= 2:
                    if func.name == 'TO_CHAR':
                        # PostgreSQL TO_CHAR format
                        format_strings = ['YYYY-MM-DD', 'YYYY-MM-DD HH24:MI:SS', 'DD-MON-YYYY', 'HH24:MI:SS']
                    else:
                        # MySQL DATE_FORMAT format
                        format_strings = ['%Y-%m-%d', '%Y-%m-%d %H:%i:%s', '%d-%b-%Y', '%H:%i:%s']
                    literal = LiteralNode(random.choice(format_strings), 'STRING')
                    func_node.add_child(literal)
            elif func.name == 'CONCAT':
                # CONCAT function special handling: ensure all parameters are string type
                for param_idx in range(func.min_params):
                    # Try to find string type columns
                    string_columns = []
                    for table in tables_to_choose:
                        string_columns.extend([col for col in table.columns if col.category == 'string'])
                    
                    if string_columns:
                        col = random.choice(string_columns)
                        table = [t for t in tables_to_choose if col in t.columns][0]
                        alias = main_alias if table == main_table else join_alias
                        # Mark column as used in select
                        if column_tracker:
                            column_tracker.mark_column_as_select(alias, col.name)
                        col_ref = ColumnReferenceNode(col, alias)
                    else:
                        # No string type columns, use literal string
                        col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                    func_node.add_child(col_ref)
            else:
                # Use general logic for other functions
                for param_idx in range(func.min_params):
                    table = random.choice(tables_to_choose)
                    alias = main_alias if table == main_table else join_alias
                    # Use column tracker to select columns not used in select, having, and on
                    col = get_random_column_with_tracker(table, alias, column_tracker, for_select=True)
                    # Mark column as used in select
                    if column_tracker:
                        column_tracker.mark_column_as_select(alias, col.name)
                    col_ref = ColumnReferenceNode(col, alias)
                    func_node.add_child(col_ref)
            
            return func_node
    
    # Final fallback: use literals matching the type
    if expr_type == 'numeric':
        return LiteralNode(random.randint(1, 100), 'INT')
    elif expr_type == 'string':
            return LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
    elif expr_type == 'datetime':
        return LiteralNode('2023-01-01', 'DATE')
    else:
        return LiteralNode(random.randint(1, 100), 'INT')


def create_in_subquery(tables: List[Table], functions: List[Function], 
                      from_node: FromNode, main_table: Table, main_alias: str, 
                      join_table: Optional[Table] = None, join_alias: Optional[str] = None, 
                      column_tracker: Optional[ColumnUsageTracker] = None) -> ASTNode:
    """Create IN/NOT IN subqueries, including various anti-join forms"""
    if random.random() < 0.3 and len(tables) > 1:
        # Select a different table for subquery
        subquery_table = random.choice([t for t in tables if t != main_table])
        
        # Create subquery
        subquery = SelectNode()
        subquery.tables = [subquery_table]
        subquery.functions = functions
        
        # Add simple SELECT expressions
        subquery_col = subquery_table.get_random_column('numeric')
        subquery_expr = ColumnReferenceNode(subquery_col, 'subq')
        subquery.add_select_expression(subquery_expr)
        
        # Create FROM clause
        subquery_from = FromNode()
        subquery_from.add_table(subquery_table, 'subq')
        subquery.set_from_clause(subquery_from)
        
        # Add WHERE condition (optional)
        if random.random() > 0.5:
            subquery_where = ComparisonNode('>')
            subquery_where.add_child(ColumnReferenceNode(subquery_col, 'subq'))
            subquery_where.add_child(LiteralNode(random.randint(0, 50), 'INT'))
            subquery.set_where_clause(subquery_where)
        
        # Left side column reference - use column tracker to select columns not used in select, having, and on
        main_col = get_random_column_with_tracker(main_table, main_alias, column_tracker, for_select=False)
        left_col_ref = ColumnReferenceNode(main_col, main_alias)
        
        # Right side subquery
        subquery_node = SubqueryNode(subquery, '')
        
        # Randomly select subquery form
        subquery_form = random.random()
        if subquery_form < 0.33:
            # Standard IN/NOT IN form
            comp_node = ComparisonNode('IN' if random.random() < 0.5 else 'NOT IN')
            comp_node.add_child(left_col_ref)
            comp_node.add_child(subquery_node)
        elif subquery_form < 0.66:
            # IN (SELECT ...) IS NOT TRUE form
            inner_comp = ComparisonNode('IN')
            inner_comp.add_child(left_col_ref)
            inner_comp.add_child(subquery_node)
            outer_comp = ComparisonNode('IS NOT TRUE')
            outer_comp.add_child(inner_comp)
            comp_node = outer_comp
        else:
            # IN (SELECT ...) IS FALSE form
            inner_comp = ComparisonNode('IN')
            inner_comp.add_child(left_col_ref)
            inner_comp.add_child(subquery_node)
            outer_comp = ComparisonNode('IS FALSE')
            outer_comp.add_child(inner_comp)
            comp_node = outer_comp
        
        return comp_node
    else:
        # Fall back to simple comparison
        return create_where_condition(tables, functions, from_node, main_table, main_alias, join_table, join_alias, column_tracker=column_tracker)

def create_exists_subquery(tables: List[Table], functions: List[Function], 
                           from_node: FromNode, main_table: Table, main_alias: str, 
                           join_table: Optional[Table] = None, join_alias: Optional[str] = None, 
                           column_tracker: Optional[ColumnUsageTracker] = None) -> ASTNode:
    """Create EXISTS/NOT EXISTS subqueries, ensuring only columns actually selected in the subquery are referenced"""
    if random.random() < 0.2 and len(tables) > 1:
        # Select related table
        rel_table = random.choice(tables)
        # Create correlated subquery
        subquery = SelectNode()
        subquery.tables = [rel_table]  # Only include tables actually added to FROM clause
        subquery.functions = functions
        
        # Refer to from clause generation process in generate_random_sql
        # Create FROM clause
        subquery_from = FromNode()
        
        # Generate safe table alias to avoid SQL keyword conflicts
        sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}
        base_rel_alias = rel_table.name[:3].lower()
        rel_alias = base_rel_alias if base_rel_alias not in sql_keywords else rel_table.name[:2].lower() + str(random.randint(0, 9))
        # Add related table to subquery FROM clause
        subquery_from.add_table(rel_table, rel_alias)
        subquery.set_from_clause(subquery_from)
        
        # Select column from related table - use column tracker to select columns not used in select, having, and on
        rel_col = get_random_column_with_tracker(rel_table, rel_alias, column_tracker, for_select=True)
        
        # Add SELECT expressions, use random columns from related table and set alias
        if rel_col:
            subquery_expr = ColumnReferenceNode(rel_col, rel_alias)
            # Set column alias to ensure clear reference to subquery returned column
            subquery.add_select_expression(subquery_expr, rel_col.name)
        else:
            # If no column found, use default constant
            subquery_expr = LiteralNode(1, 'INT')
            subquery.add_select_expression(subquery_expr, 'default_col')
        
        # Create WHERE condition (correlation condition for correlated subquery)
        where_node = ComparisonNode('=')
        
        if rel_col:
            left_col_ref = ColumnReferenceNode(rel_col, rel_alias)
            
            # Select right operand based on column type (use constant directly)
            if rel_col.category == 'numeric':
                # Numeric type: use numeric constant directly
                numeric_value = random.randint(0, 100)
                where_node.add_child(left_col_ref)
                where_node.add_child(LiteralNode(numeric_value, rel_col.data_type))
            elif rel_col.category == 'string':
                # String type: use string constant directly
                string_value = f"sample_{random.randint(1, 100)}"
                where_node.add_child(left_col_ref)
                where_node.add_child(LiteralNode(string_value, 'STRING'))
            elif rel_col.category == 'datetime':
                # Datetime type: generate datetime constant with time part
                # Ensure explicit DATETIME type to ensure quotes are added correctly
                year = 2023
                month = random.randint(1, 12)
                day = random.randint(1, 28)  # Simple handling to avoid end-of-month issues
                hour = random.randint(0, 23)
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                
                # Construct complete date/time string
                datetime_value = f"'{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}'"
                
                # Use explicit DATETIME type to ensure quotes are added
                datetime_type = 'DATETIME'  # Explicitly use DATETIME type to ensure quotes are added
                where_node.add_child(left_col_ref)
                where_node.add_child(LiteralNode(datetime_value, datetime_type))
            else:
                # Other types: use default string constant directly
                default_value = "default_value"
                where_node.add_child(left_col_ref)
                where_node.add_child(LiteralNode(default_value, 'STRING'))
        else:
            # If no suitable column found, use default condition
            where_node.add_child(LiteralNode(1, 'INT'))
            where_node.add_child(LiteralNode(1, 'INT'))
        
        if where_node.children:
            subquery.set_where_clause(where_node)
        
        # Validate column references inside subquery to ensure no non-existent columns are referenced
        valid, invalid_columns = subquery.validate_all_columns()
        if not valid and invalid_columns:
            # If invalid column references exist, simplify WHERE condition
            where_node = ComparisonNode('=')
            where_node.add_child(LiteralNode(1, 'INT'))
            where_node.add_child(LiteralNode(1, 'INT'))
            subquery.set_where_clause(where_node)
        
        # Note: EXISTS/NOT EXISTS subqueries should not have aliases
        subquery_node = SubqueryNode(subquery, '')
        
        # Randomly select subquery form
        subquery_form = random.random()
        if subquery_form < 0.33:
            # Standard EXISTS/NOT EXISTS form
            exists_node = ComparisonNode('EXISTS' if random.random() < 0.5 else 'NOT EXISTS')
            exists_node.add_child(subquery_node)
            return exists_node
        elif subquery_form < 0.66:
            # EXISTS (SELECT ...) IS NOT TRUE form
            exists_comp = ComparisonNode('EXISTS')
            exists_comp.add_child(subquery_node)
            outer_comp = ComparisonNode('IS NOT TRUE')
            outer_comp.add_child(exists_comp)
            return outer_comp
        else:
            # EXISTS (SELECT ...) IS FALSE form
            exists_comp = ComparisonNode('EXISTS')
            exists_comp.add_child(subquery_node)
            outer_comp = ComparisonNode('IS FALSE')
            outer_comp.add_child(exists_comp)
            return outer_comp
    else:
        return create_where_condition(tables, functions, from_node, main_table, main_alias, join_table, join_alias, column_tracker=column_tracker)

def create_where_condition(tables: List[Table], functions: List[Function], 
                          from_node: FromNode, main_table: Table, main_alias: str, 
                          join_table: Optional[Table] = None, join_alias: Optional[str] = None, 
                          use_subquery: bool = True, column_tracker: Optional[ColumnUsageTracker] = None) -> ASTNode:
    """Create WHERE conditions, supporting multiple condition types"""
    
    # Filter out aggregate functions to ensure WHERE conditions don't contain aggregate functions
    non_aggregate_functions = [f for f in functions if f.func_type != 'aggregate']
    
    # Select condition type based on probability
    condition_type = random.random()
    

    # 15% probability to use subquery conditions (IN/NOT IN or EXISTS/NOT EXISTS)
    if use_subquery and condition_type < 0.15:
        # 50% probability to use IN/NOT IN subquery, 50% probability to use EXISTS/NOT EXISTS subquery
        if random.random() < 0.5:
            return create_in_subquery(tables, non_aggregate_functions, from_node, main_table, main_alias, join_table, join_alias, column_tracker)
        else:
            return create_exists_subquery(tables, non_aggregate_functions, from_node, main_table, main_alias, join_table, join_alias, column_tracker)
    
    # 15% probability to use subqueries with ANY/ALL (new predicate 1)
    elif use_subquery and condition_type < 0.3:
        # Select table and column
        tables_to_choose = [main_table] + ([join_table] if join_table else [])
        table = random.choice(tables_to_choose)
        alias = main_alias if table == main_table else join_alias
        
        # Prioritize numeric columns
        numeric_cols = []
        if column_tracker:
            available_columns = column_tracker.get_available_columns(table, alias)
            numeric_cols = [col for col in available_columns if col.category in ['numeric', 'binary']]
        else:
            numeric_cols = [col for col in table.columns if col.category in ['numeric', 'binary']]
            
        if numeric_cols:
            col = random.choice(numeric_cols)
            if column_tracker:
                column_tracker.mark_column_as_used(alias, col.name)
            
            col_ref = ColumnReferenceNode(col, alias)
            
            # Create subquery
            subquery = SelectNode()
            subquery_table = random.choice([t for t in tables if t != table]) if len(tables) > 1 else table
            subquery_col = subquery_table.get_random_column(col.category)
            subquery.add_select_expression(ColumnReferenceNode(subquery_col, 'subq'))
            subquery_from = FromNode()
            subquery_from.add_table(subquery_table, 'subq')
            subquery.set_from_clause(subquery_from)
            
            # Select ANY/ALL operator and comparison operator
            any_all = random.choice(['ANY', 'ALL'])
            # Select appropriate comparison operator for binary types
            if col.category == 'binary':
                operator = random.choice(['=', '!='])
            else:
                operator = random.choice(['=', '!=', '<', '>', '<=', '>='])
            
            comp_node = ComparisonNode(f'{operator} {any_all}')
            comp_node.add_child(col_ref)
            comp_node.add_child(SubqueryNode(subquery, ''))
            
            return comp_node
        
        # If no numeric columns, fall back to simple comparison
        return create_where_condition(tables, non_aggregate_functions, from_node, main_table, main_alias, join_table, join_alias, False)
    
    # 15% probability to use complex nested expressions
    elif condition_type < 0.45:
        return create_complex_expression(tables, non_aggregate_functions, from_node, main_table, main_alias, join_table, join_alias, column_tracker=column_tracker, for_select=False)
    
    # 15% probability to use BETWEEN condition
    elif condition_type < 0.6:
        # Select table and column
        tables_to_choose = [main_table] + ([join_table] if join_table else [])
        table = random.choice(tables_to_choose)
        alias = main_alias if table == main_table else join_alias
        
        # Prioritize numeric or date type columns
        numeric_cols = []
        if column_tracker:
            # Get unused numeric or date type columns
            available_columns = column_tracker.get_available_columns(table, alias)
            numeric_cols = [col for col in available_columns if col.category in ['numeric', 'datetime', 'binary']]
        else:
            # If no tracker, use all numeric or date type columns
            numeric_cols = [col for col in table.columns if col.category in ['numeric', 'datetime', 'binary']]
            
        if numeric_cols:
            col = random.choice(numeric_cols)
            if column_tracker:
                column_tracker.mark_column_as_used(alias, col.name)
            
            col_ref = ColumnReferenceNode(col, alias)
            
            # Create BETWEEN condition
            between_node = ComparisonNode('BETWEEN' if random.random() < 0.5 else 'NOT BETWEEN')
            between_node.add_child(col_ref)
            
            # Add low and high values
            if col.category == 'numeric':
                low_value = random.randint(0, 50)
                high_value = low_value + random.randint(10, 50)
                low_node = LiteralNode(low_value, col.data_type)
                high_node = LiteralNode(high_value, col.data_type)
                
                # Add low and high values directly
                between_node.add_child(low_node)
                between_node.add_child(high_node)
            elif col.category == 'binary':
                # Generate random hexadecimal value
                low_hex = ''.join(random.choices('0123456789ABCDEF', k=8))
                high_hex = ''.join(random.choices('0123456789ABCDEF', k=8))
                low_node = LiteralNode(f"X'{low_hex}'", 'BINARY')
                high_node = LiteralNode(f"X'{high_hex}'", 'BINARY')
                between_node.add_child(low_node)
                between_node.add_child(high_node)
            elif col.category == 'datetime':
                # Generate complete datetime string including year, month, day, hour, minute, second
                start_datetime = f"2023-{random.randint(1, 12):02d}-{random.randint(1, 28):02d} {random.randint(0, 23):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"
                # Ensure end datetime is greater than start datetime
                end_month = random.randint(1, 12)
                end_day = random.randint(1, 28)
                end_hour = random.randint(0, 23)
                end_minute = random.randint(0, 59)
                end_second = random.randint(0, 59)
                end_datetime = f"2023-{end_month:02d}-{end_day:02d} {end_hour:02d}:{end_minute:02d}:{end_second:02d}"
                
                # Use explicit 'DATETIME' type to ensure quotes are added
                start_node = LiteralNode(start_datetime, 'DATETIME')
                end_node = LiteralNode(end_datetime, 'DATETIME')
                
                # Add start and end values directly
                between_node.add_child(start_node)
                between_node.add_child(end_node)
            
            return between_node
        else:
            # If no numeric or date type columns, fall back to conditions suitable for other types
            col = get_random_column_with_tracker(table, alias, column_tracker, for_select=False)
            col_ref = ColumnReferenceNode(col, alias)
            
            # Select appropriate operator based on column type
            if col.category == 'string':
                operator = random.choice(['=', '<>', 'LIKE', 'NOT LIKE'])
                comp_node = ComparisonNode(operator)
                comp_node.add_child(col_ref)
                
                if operator in ['LIKE', 'NOT LIKE']:
                    patterns = [
                        f"sample_{random.randint(1, 100)}",
                        f"%sample_{random.randint(1, 100)}",
                        f"sample_{random.randint(1, 100)}%",
                        f"%sample_{random.randint(1, 100)}%"
                    ]
                    selected_pattern = random.choice(patterns)
                    comp_node.add_child(LiteralNode(selected_pattern, 'STRING'))
                else:
                    sample_value = f"sample_{random.randint(1, 100)}"
                    comp_node.add_child(LiteralNode(sample_value, 'STRING'))
            else:
                # For other types (like geometric types), use IS NULL/IS NOT NULL operators
                operator = random.choice(['IS NULL', 'IS NOT NULL'])
                comp_node = ComparisonNode(operator)
                comp_node.add_child(col_ref)
                
                # IS NULL/IS NOT NULL doesn't need right operand
                if operator not in ['IS NULL', 'IS NOT NULL']:
                    # For safety, use string literal as default value
                    sample_value = f"sample_{random.randint(1, 100)}"
                    comp_node.add_child(LiteralNode(sample_value, 'STRING'))
            
            return comp_node
    
    # 15% probability to use enhanced regex patterns (new predicate 2)
    elif condition_type < 0.75 and join_table:
        # Select table and column
        tables_to_choose = [main_table, join_table]
        table = random.choice(tables_to_choose)
        alias = main_alias if table == main_table else join_alias
        
        # Prioritize string columns
        string_cols = []
        if column_tracker:
            # Get unused string columns
            available_columns = column_tracker.get_available_columns(table, alias)
            string_cols = [col for col in available_columns if col.category == 'string']
        else:
            # If no tracker, use all string columns
            string_cols = [col for col in table.columns if col.category == 'string']
            
        if string_cols:
            col = random.choice(string_cols)
            if column_tracker:
                column_tracker.mark_column_as_used(alias, col.name)
            col_ref = ColumnReferenceNode(col, alias)
            
            # Randomly select operator
            operator = random.choice(['LIKE', 'NOT LIKE', 'RLIKE', 'REGEXP', 'NOT REGEXP'])
            
            comp_node = ComparisonNode(operator)
            comp_node.add_child(col_ref)
            
            # Add enhanced regex pattern
            if operator in ['RLIKE', 'REGEXP', 'NOT REGEXP']:
                # More complex regex patterns
                complex_patterns = [
                    r'[a-zA-Z0-9]{5,10}',
                    r'[A-Z][a-z]{2,4}[0-9]{2}',
                    r'[0-9]{3}-[0-9]{2}-[0-9]{4}',
                    r'[a-z]+@[a-z]+\.[a-z]{2,3}',
                    r'^sample_\d+$',
                    r'.*[0-9]{3}.*'
                ]
                selected_pattern = random.choice(complex_patterns)
            else:
                patterns = [
                    f"sample_{random.randint(1, 100)}",
                    f"%sample_{random.randint(1, 100)}",
                    f"sample_{random.randint(1, 100)}%",
                    f"%sample_{random.randint(1, 100)}%"
                ]
                selected_pattern = random.choice(patterns)
                
            # Ensure string literals use correct data type identifier
            string_type = 'STRING'
            pattern_node = LiteralNode(selected_pattern, string_type)
            
            comp_node.add_child(pattern_node)
            
            return comp_node
        
        # If no string columns, fall back to simple comparison
        return create_where_condition(tables, non_aggregate_functions, from_node, main_table, main_alias, join_table, join_alias, False)
            
        if string_cols:
            col = random.choice(string_cols)
            if column_tracker:
                column_tracker.mark_column_as_used(alias, col.name)
            col_ref = ColumnReferenceNode(col, alias)
            
            # Randomly select operator
            operator = random.choice(['LIKE', 'NOT LIKE', 'RLIKE', 'REGEXP', 'NOT REGEXP'])
            
            comp_node = ComparisonNode(operator)
            comp_node.add_child(col_ref)
            
            # Add pattern
            patterns = [
                f"sample_{random.randint(1, 100)}",
                f"%sample_{random.randint(1, 100)}",
                f"sample_{random.randint(1, 100)}%",
                f"%sample_{random.randint(1, 100)}%",
                f"[a-z]{{3,5}}" if operator in ['RLIKE', 'REGEXP', 'NOT REGEXP'] else None
            ]
            # Filter out None values
            valid_patterns = [p for p in patterns if p is not None]
            selected_pattern = random.choice(valid_patterns)
            # Ensure string literals use correct data type identifier, ensure proper quote handling
            string_type = 'STRING'  # Explicitly use STRING type to ensure quotes are added
            pattern_node = LiteralNode(selected_pattern, string_type)
            
            comp_node.add_child(pattern_node)
            
            return comp_node
    
    # 15% probability to use NULL-safe comparison (new predicate 3)
    elif condition_type < 0.9:
        # Select table and column
        tables_to_choose = [main_table] + ([join_table] if join_table else [])
        table = random.choice(tables_to_choose)
        alias = main_alias if table == main_table else join_alias
        col = get_random_column_with_tracker(table, alias, column_tracker, for_select=False)
        
        # Avoid using unsupported NULL-safe comparison operators, use standard comparison operators combined with IS NULL instead
        # Select different comparison methods based on whether the right side is NULL
        if random.random() < 0.5:
            # Use IS NULL when right side is NULL
            comp_node = ComparisonNode('IS NULL')
            comp_node.add_child(ColumnReferenceNode(col, alias))
        else:
            # Use standard comparison operator when right side is a specific value
            operator = random.choice(['=', '<>', '!=', '<', '>', '<=', '>='])
            comp_node = ComparisonNode(operator)
            comp_node.add_child(ColumnReferenceNode(col, alias))
        
        # Randomly select right operand as NULL or specific value
        if random.random() < 0.5:
            # Right side is NULL
            comp_node.add_child(LiteralNode(None, 'NULL'))
        else:
            # Right side is specific value
            if col.category == 'numeric':
                value = random.randint(0, 100)
                comp_node.add_child(LiteralNode(value, col.data_type))
            elif col.category == 'string':
                value = f"sample_{random.randint(1, 100)}"
                comp_node.add_child(LiteralNode(value, 'STRING'))
            elif col.category == 'datetime':
                year = 2023
                month = random.randint(1, 12)
                day = random.randint(1, 28)
                datetime_value = f"{year:04d}-{month:02d}-{day:02d}"
                comp_node.add_child(LiteralNode(datetime_value, 'DATE'))
            elif col.category == 'binary':
                # Generate random hexadecimal value for binary type
                hex_value = ''.join(random.choices('0123456789ABCDEF', k=8))
                comp_node.add_child(LiteralNode(f"X'{hex_value}'", 'BINARY'))
            elif col.category == 'binary':
                # Generate random hexadecimal value for binary type
                hex_value = ''.join(random.choices('0123456789ABCDEF', k=8))
                comp_node.add_child(LiteralNode(f"X'{hex_value}'", 'BINARY'))
        
        return comp_node
    
    # 10% probability to use multi-column range comparison (new predicate 4)
    else:
        # Multi-column range comparison can only be used when there is a join table
        if join_table:
            # Select two related numeric columns, ensuring selection through column tracker
                main_numeric_cols = []
                join_numeric_cols = []
                
                # Use column tracker to get available numeric columns
                if column_tracker:
                    main_available_columns = column_tracker.get_available_columns(main_table, main_alias)
                    main_numeric_cols = [col for col in main_available_columns if col.category in ['numeric', 'binary']]
                    
                    join_available_columns = column_tracker.get_available_columns(join_table, join_alias)
                    join_numeric_cols = [col for col in join_available_columns if col.category in ['numeric', 'binary']]
                else:
                    # If no column tracker, use all numeric columns
                    main_numeric_cols = [col for col in main_table.columns if col.category in ['numeric', 'binary']]
                join_numeric_cols = [col for col in join_table.columns if col.category in ['numeric', 'binary']]
                
                if main_numeric_cols and join_numeric_cols:
                    # Create multi-column range comparison (e.g., (a, b) BETWEEN (x, y) AND (p, q))
                    # First create logical AND node to combine multiple conditions
                    logic_node = LogicalNode('AND')
                    
                    # Select 2-3 pairs of columns for comparison
                    num_pairs = random.randint(2, 3)
                    for _ in range(num_pairs):
                        # Use column tracker to select columns not used in select, having, and on
                        main_col = get_random_column_with_tracker(main_table, main_alias, column_tracker, for_select=False)
                        join_col = get_random_column_with_tracker(join_table, join_alias, column_tracker, for_select=False)
                    
                    # Create BETWEEN condition for each pair of columns
                    between_node = ComparisonNode('BETWEEN')
                    between_node.add_child(ColumnReferenceNode(join_col, join_alias))
                    
                    # Generate range values
                    low_value = random.randint(0, 30)
                    high_value = low_value + random.randint(10, 50)
                    
                    # Use main table's column value as basis to generate related range
                    # Here use literals instead of actual column reference comparison
                    between_node.add_child(LiteralNode(low_value, main_col.data_type))
                    between_node.add_child(LiteralNode(high_value, main_col.data_type))
                    
                    logic_node.add_child(between_node)
                
                return logic_node
        
        # Default: use simple comparison condition
        # Select table and column
        tables_to_choose = [main_table] + ([join_table] if join_table else [])
        table = random.choice(tables_to_choose)
        alias = main_alias if table == main_table else join_alias
        col = get_random_column_with_tracker(table, alias, column_tracker, for_select=False)
        col_ref = ColumnReferenceNode(col, alias)
        # Select operator based on column type
        if col.category == 'string':
            operator = random.choice(['=', '<>', 'LIKE', 'NOT LIKE', 'IS NULL', 'IS NOT NULL'])
        elif col.category == 'binary':
            # Binary type uses equals, not equals, or IS NULL/IS NOT NULL operators
            operator = random.choice(['=', '<>', 'IS NULL', 'IS NOT NULL'])
        else:
            operator = random.choice(['=', '<>', '<', '>', '<=', '>=', 'IS NULL', 'IS NOT NULL'])
        comp_node = ComparisonNode(operator)
        comp_node.add_child(col_ref)
        
        # Add right operand (ensure type compatibility)
        if operator not in ['IS NULL', 'IS NOT NULL']:
            if col.category == 'numeric':
                numeric_value = random.randint(0, 100)
                # Create numeric literal compatible with column type
                right_node = LiteralNode(numeric_value, col.data_type)
                comp_node.add_child(right_node)
            elif col.category == 'binary':
                # Generate compatible hexadecimal value for binary type
                hex_value = ''.join(random.choices('0123456789ABCDEF', k=8))
                right_node = LiteralNode(f"X'{hex_value}'", 'BINARY')
                comp_node.add_child(right_node)
            elif col.category == 'string':
                # Create string literal compatible with column type
                if operator in ['LIKE', 'NOT LIKE']:
                    # Generate pattern containing wildcards
                    patterns = [
                        f"sample_{random.randint(1, 100)}",
                        f"%sample_{random.randint(1, 100)}",
                        f"sample_{random.randint(1, 100)}%",
                        f"%sample_{random.randint(1, 100)}%"
                    ]
                    selected_pattern = random.choice(patterns)
                    # Ensure string literals are properly quoted
                    right_node = LiteralNode(selected_pattern, 'STRING')
                else:
                    string_value = f"sample_{random.randint(1, 100)}"
                    # Explicitly use 'STRING' type to ensure proper string quoting
                    right_node = LiteralNode(string_value, 'STRING')
                
                comp_node.add_child(right_node)
            elif col.category == 'datetime':
                # Create date/time literals compatible with column type, using proper formatting to avoid syntax errors
                from data_structures.db_dialect import get_dialect_config
                dialect = get_dialect_config()
                
                # Generate more specific date/time values including time portion
                # Directly use the already imported random module
                year = 2023
                month = random.randint(1, 12)
                day = random.randint(1, 28)  # Simple handling to avoid end-of-month issues
                hour = random.randint(0, 23)
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                
                # Build complete datetime string
                datetime_value = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
                
                # Use explicit date/time type to ensure proper quoting
                # Even if col.data_type is not standard DATE/DATETIME/TIMESTAMP, use explicit type
                datetime_type = 'DATETIME'  # Explicitly use DATETIME type to ensure quotes are added
                right_node = LiteralNode(datetime_value, datetime_type)
                
                comp_node.add_child(right_node)
        
        return comp_node


def generate_random_sql(tables: List[Table], functions: List[Function], current_depth: int = 0) -> str:
    """Generate random SQL query"""
    use_cte = False
    # Get current database dialect
    current_dialect = get_current_dialect()
    # Check if it's Percona dialect
    # Now allow Percona to use WITH query functionality
    is_percona = current_dialect and 'PerconaDialect' == current_dialect.__class__.__name__
    # First attempt to generate CTE query, including Percona dialect
    if random.random() > 0.3:  # All dialects supporting WITH clause, including Percona, have the same probability to generate WITH type query
        use_cte = True
        with_node = WithNode()
        
        # Generate 1-2 CTEs
        num_ctes = random.randint(1, 2)
        for i in range(num_ctes):
            cte_name = f"cte_{random.randint(1, 999)}"
            cte_query = SelectNode()
            # Initialize column usage tracker for CTE query
            cte_column_tracker = ColumnUsageTracker()
            cte_query.tables = tables
            cte_query.functions = functions
            # Store column tracker for later use
            cte_query.metadata = {'column_tracker': cte_column_tracker}
            
            # Select a table for CTE
            cte_table = random.choice(tables)
            cte_alias = generate_table_alias()
            
            # Create FROM clause
            cte_from = FromNode()
            cte_from.add_table(cte_table, cte_alias)
            cte_query.set_from_clause(cte_from)
            # Initialize available columns information for CTE query's column tracker
            if cte_query.metadata and 'column_tracker' in cte_query.metadata:
                cte_column_tracker = cte_query.metadata['column_tracker']
                if hasattr(cte_column_tracker, 'initialize_from_from_node'):
                    cte_column_tracker.initialize_from_from_node(cte_from)
            
            # Add SELECT expressions
            num_columns = random.randint(2, 4)
            non_aggregate_columns = []  # Store non-aggregate columns
            has_aggregate_function = False  # Track if aggregate functions are included
            for j in range(num_columns):
                # Randomly add
                expr = create_random_expression([cte_table], functions, cte_from, cte_table, cte_alias, use_subquery=False,column_tracker=cte_column_tracker, for_select=True)
                cte_query.add_select_expression(expr, f"col_{j+1}")
                
            # New GROUP BY clause addition logic: generate all expressions first, then handle accordingly
            # Create GroupByNode
            cte_group_by = GroupByNode()
            # Check if aggregate functions are included
            has_aggregate = False
            
            if hasattr(cte_query, 'select_expressions'):
                try:
                    # Try to iterate through select_expressions
                    for expr, alias in cte_query.select_expressions:
                        # Check if expression has function attribute
                        if hasattr(expr, 'function'):
                            if getattr(expr.function, 'func_type', '') == 'aggregate':
                                has_aggregate = True
                except Exception as e:
                    print(f"Error while iterating through cte_query.select_expressions: {e}")
            
            # If there are aggregate functions, add GROUP BY clause
            if has_aggregate:
                # Collect all non-aggregate columns and add to GROUP BY
                for expr, alias in cte_query.select_expressions:
                    if not (hasattr(expr, 'function') and getattr(expr.function, 'func_type', '') == 'aggregate'):
                        # Determine if it's a scalar function column or column reference
                        if (hasattr(expr, 'function') and getattr(expr.function, 'func_type', '') == 'scalar') or type(expr).__name__ == 'ColumnReferenceNode':
                            # Add expression directly to GROUP BY
                            cte_group_by.add_expression(expr)
                        # Determine window function
                        elif hasattr(expr, 'function') and getattr(expr.function, 'func_type', '') == 'window':
                            if hasattr(expr, 'metadata'):
                                # Handle partition_by
                                if expr.metadata.get('partition_by'):
                                    partition_by = expr.metadata.get('partition_by')
                                    # Try to get cte_from (assuming it's available in parent scope)
                                    if 'cte_from' in locals() or 'cte_from' in globals():
                                        available_from_node = locals().get('cte_from') or globals().get('cte_from')
                                        
                                        for part_expr in partition_by:
                                            try:
                                                # Parse table alias and column name
                                                if '.' in part_expr:
                                                    alias_part, col_part = part_expr.split('.', 1)
                                                    # Clean possible quotes
                                                    alias_part = alias_part.strip('"\'')
                                                    col_part = col_part.strip('"\'')
                                                    
                                                    # Get table object
                                                    table_ref = available_from_node.get_table_for_alias(alias_part)
                                                    if table_ref and hasattr(table_ref, 'get_column'):
                                                        # Get column object
                                                        col = table_ref.get_column(col_part)
                                                        if col:
                                                            # Create ColumnReferenceNode object
                                                            col_ref = ColumnReferenceNode(col, alias_part)
                                                            # Add to GROUP BY
                                                            cte_group_by.add_expression(col_ref)
                                            except Exception as e:
                                                print(f"  Error while converting partition_by expression: {e}")
                                
                                # Handle order_by
                                if expr.metadata.get('order_by'):
                                    order_by = expr.metadata.get('order_by')
                                    # Try to get cte_from (assuming it's available in parent scope)
                                    if 'cte_from' in locals() or 'cte_from' in globals():
                                        available_from_node = locals().get('cte_from') or globals().get('cte_from')
                                        
                                        # Handle order_by expressions
                                        main_expr = []
                                        for order in order_by:
                                            expr_parts = order.rsplit(' ', 1)
                                            if len(expr_parts) == 2 and expr_parts[1].upper() in ['ASC', 'DESC']:
                                                main_expr.append(expr_parts[0])
                                            else:
                                                main_expr.append(order)
                                        
                                        for part_expr in main_expr:
                                            try:
                                                # Parse table alias and column name
                                                if '.' in part_expr:
                                                    alias_part, col_part = part_expr.split('.', 1)
                                                    # Clean up possible quotes
                                                    alias_part = alias_part.strip('"\'')
                                                    col_part = col_part.strip('"\'')
                                                    
                                                    # Get table object
                                                    table_ref = available_from_node.get_table_for_alias(alias_part)
                                                    if table_ref and hasattr(table_ref, 'get_column'):
                                                        # Get column object
                                                        col = table_ref.get_column(col_part)
                                                        if col:
                                                            # Create ColumnReferenceNode object
                                                            col_ref = ColumnReferenceNode(col, alias_part)
                                                            # Add to GROUP BY
                                                            cte_group_by.add_expression(col_ref)
                                            except Exception as e:
                                                print(f"  Error converting order_by expression: {e}")
                
                # Set GROUP BY clause
                if cte_group_by.expressions:
                    cte_query.set_group_by_clause(cte_group_by)
            
            # Add WHERE condition
            if random.random() > 0.5:
                # Ensure WHERE condition only references columns that actually exist in the CTE
                # Ensure column_tracker exists (initialized earlier)
                cte_column_tracker = cte_query.metadata.get('column_tracker')
                where = create_where_condition([cte_table], functions, cte_from, cte_table, cte_alias, use_subquery=False, column_tracker=cte_column_tracker)
                cte_query.set_where_clause(where)
                
                # Validate and fix column references in WHERE condition
                if hasattr(cte_query, 'validate_all_columns'):
                    valid, errors = cte_query.validate_all_columns()
                    if not valid and hasattr(cte_query, 'repair_invalid_columns'):
                        cte_query.repair_invalid_columns()
            
            # Add CTE to WITH clause
            with_node.add_cte(cte_name, cte_query, num_columns)
        
        # Generate main query using CTEs and actual tables
        main_query = SelectNode()
        main_query.tables = tables
        main_query.functions = functions
        
        main_from = FromNode()
        
        # Create virtual tables representing all CTEs
        cte_tables = []
        for cte_name, cte_query, cte_num_columns in with_node.ctes:
            # Create virtual table
            # Try to extract actual column information from cte_query
            columns = []
            
            # Check if cte_query has select_expressions attribute
            if hasattr(cte_query, 'select_expressions') and cte_query.select_expressions:
                # Iterate through select_expressions and extract column information
                for j, (expr, alias) in enumerate(cte_query.select_expressions):
                    # Default values
                    col_name = alias or f"col_{j+1}"
                    data_type = "INT"
                    category = "numeric"
                    is_nullable = False
                    
                    # Try to get actual type information from expression
                    if hasattr(expr, 'metadata') and expr.metadata:
                        if 'data_type' in expr.metadata:
                            data_type = expr.metadata['data_type']
                        if 'category' in expr.metadata:
                            category = expr.metadata['category']
                        if 'is_nullable' in expr.metadata:
                            is_nullable = expr.metadata['is_nullable']
                    elif hasattr(expr, 'column'):
                        # If it's a column reference, use original column information
                        column = expr.column
                        if hasattr(column, 'data_type'):
                            data_type = column.data_type
                        if hasattr(column, 'category'):
                            category = column.category
                        if hasattr(column, 'is_nullable'):
                            is_nullable = column.is_nullable
                    elif hasattr(expr, 'function'):
                        # If it's a function call, infer based on function type
                        func = expr.function
                        if hasattr(func, 'return_type'):
                            data_type = func.return_type
                        if hasattr(func, 'return_category'):
                            category = func.return_category
                    
                    # Create column
                    columns.append(Column(
                        name=col_name,
                        data_type=data_type,
                        category=category,
                        is_nullable=is_nullable,
                        table_name=cte_name
                    ))
            else:
                # If actual information cannot be obtained, fall back to default method
                columns = [Column(f"col_{j+1}", "INT", "numeric", False, cte_name) for j in range(cte_num_columns)]
            
            # Create CTE virtual table
            cte_table = Table(
                name=cte_name,
                columns=columns,
                primary_key=columns[0].name if columns else "col_1",
                foreign_keys=[]
            )
            # Add column_alias_map attribute to allow column tracker to correctly identify and track columns in these virtual tables
            cte_table.column_alias_map = {}
            for col in columns:
                cte_table.column_alias_map[col.name] = (col.name, col.data_type, col.category)
            cte_tables.append(cte_table)
        
        # Merge CTE virtual tables with original tables
        combined_tables = tables + cte_tables
        tables = combined_tables
        
    
    # Randomly decide whether to generate set operations
    if random.random() > 0.7 and current_depth == 0:  
        # 30% probability to generate set operations, and only at the top-level query
        # Select set operation type, including INTERSECT and EXCEPT
        operation_type = random.choice(['UNION', 'UNION ALL', 'INTERSECT', 'EXCEPT'])
        
        # Create set operation node
        set_op_node = SetOperationNode(operation_type)
        
        # Generate 2-3 queries for set operations
        num_queries = random.randint(2, 3)
        for i in range(num_queries):
            # Generate a query (limit depth to avoid overcomplication)
            select_node = SelectNode()
            select_node.tables = tables
            select_node.functions = functions
            # Initialize column usage tracker
            column_tracker = ColumnUsageTracker()
            select_node.metadata = {'column_tracker': column_tracker}
            
            # Randomly decide whether to use DISTINCT
            if random.random() > 0.8:
                select_node.distinct = True
            
            # Select main table
            main_table = random.choice(tables)
            main_alias = generate_table_alias()
            
            # Create FROM clause
            from_node = FromNode()
            from_node.add_table(main_table, main_alias)
            select_node.set_from_clause(from_node)
            # Initialize available columns information for column tracker
            
            
            # Randomly add join table (simplified, no more than 1 join)
            has_join = False
            join_table = None
            join_alias = None
            if random.random() > 0.5 and i == 0:  # First query may have join, other queries should be as simple as possible
                # Select a different table for join
                available_tables = [t for t in tables if t.name != main_table.name]
                if available_tables:
                    join_table = random.choice(available_tables)
                    join_alias = generate_table_alias()
                    
                    # Randomly select join type
                    join_type = random.choice(['INNER', 'LEFT','RIGHT','CROSS'])
                    # Try to create reasonable join condition
                    join_condition = create_join_condition(main_table, main_alias, join_table, join_alias)
                    from_node.add_join(join_type, join_table, join_alias, join_condition)
                    has_join = True
                    select_node.set_from_clause(from_node)
            if select_node.metadata and 'column_tracker' in select_node.metadata:
                column_tracker = select_node.metadata['column_tracker']
                if hasattr(column_tracker, 'initialize_from_from_node'):
                    column_tracker.initialize_from_from_node(from_node)
            # Generate SELECT clause - ensure column count and types are compatible across all queries
            if i == 0:  # First query determines column count and types
                num_columns = 2 + random.randint(0, 2)  # 2-4 columns
                for j in range(num_columns):
                    expr_node = create_random_expression(
                        tables, functions, from_node, main_table, main_alias, 
                        join_table if has_join else None, join_alias if has_join else None, 
                        use_subquery=False,  # Do not use subqueries in set operations
                        column_tracker=column_tracker,
                        for_select=True
                    )
                    
                    # Generate alias
                    alias = f"col_{j+1}"  # Use consistent aliases to facilitate set operations
                    select_node.add_select_expression(expr_node, alias)
            else:  # Subsequent queries need to exactly match the column count and types of the first query
                first_query = set_op_node.queries[0]
                # Ensure column count matches the first query
                for j in range(len(first_query.select_expressions)):
                    # Get column information from first query
                    first_expr, _ = first_query.select_expressions[j]
                    expr_type = first_expr.metadata.get('category', 'any')
                    
                    # Create expression of the same type
                    expr_node = create_expression_of_type(
                        expr_type, tables, functions, from_node, main_table, main_alias,
                        join_table if has_join else None, join_alias if has_join else None,
                        column_tracker= column_tracker
                    )
                    
                    # Use consistent alias
                    alias = f"col_{j+1}"
                    select_node.add_select_expression(expr_node, alias)
            
            # Generate WHERE clause (simplified to avoid complexity)
            if random.random() > 0.6:
                # Get column_tracker from select_node if it exists
                select_column_tracker = select_node.metadata.get('column_tracker') if hasattr(select_node, 'metadata') else None
                where_node = create_where_condition(
                    tables, functions, from_node, main_table, main_alias,
                    join_table if has_join else None, join_alias if has_join else None,
                    use_subquery=False,  # Do not use subqueries in set operations
                    column_tracker=select_column_tracker
                )
                select_node.set_where_clause(where_node)
            
            # Add ORDER BY and LIMIT only to the first query (ORDER BY and LIMIT for set operations typically apply to the final result)
            if i == 0:
                # Randomly add ORDER BY clause
                if random.random() > 0.7:
                    order_by = OrderByNode()
                    # Select column to sort by
                    col = main_table.get_random_column()
                    col_ref = ColumnReferenceNode(col, main_alias)
                    
                    # Check if DISTINCT is used, if so, ensure ORDER BY columns are in the SELECT list
                    if select_node.distinct:
                        # Check if already in SELECT list
                        in_select = False
                        for selected_expr, selected_alias in select_node.select_expressions:
                            if hasattr(selected_expr, 'to_sql') and selected_expr.to_sql() == col_ref.to_sql():
                                in_select = True
                                break
                        
                        # If not in SELECT list, add it
                        if not in_select:
                            select_node.add_select_expression(col_ref, col.name)
                    
                    order_by.add_expression(col_ref, random.choice(['ASC', 'DESC']))
                    select_node.set_order_by_clause(order_by)
                
                # Randomly add LIMIT clause
                if random.random() > 0.7:
                    select_node.set_limit_clause(LimitNode(random.randint(1, 5)))
            # Check if each subquery has aggregate functions and add GROUP BY clause
            # Save reference to outer select_node
            outer_select_node = select_node
            for i, expr_item in enumerate(outer_select_node.select_expressions):
                group_by = GroupByNode()
                # Check if aggregate function is included
                has_aggregate = False
                try:
                    expr, alias = expr_item
                    if hasattr(expr, 'function') and hasattr(expr.function, 'func_type') and expr.function.func_type == 'aggregate':
                        has_aggregate = True
                        
                except (TypeError, ValueError):
                    pass
                if hasattr(select_node, 'select_expressions'):
                    try:
                        # Try to iterate through select_expressions
                        expressions_count = 0
                        for expr, alias in select_node.select_expressions:
                            expressions_count += 1
                            # Check if expression has function attribute
                            if hasattr(expr, 'function'):
                                if getattr(expr.function, 'func_type', '') == 'aggregate':
                                    has_aggregate = True
                    except Exception as e:
                        print(f"Error iterating through select_expressions: {e}")
                else:
                    print("Warning: select_node does not have select_expressions attribute")
                # If there are aggregate functions, add GROUP BY clause
                if has_aggregate:
                    added_group_columns = set()
                    # Collect all non-aggregate columns and add to GROUP BY
                    for expr, alias in select_node.select_expressions:
                        if not (hasattr(expr, 'function') and expr.function.func_type == 'aggregate'):
                            # Determine if it's a scalar function column
                            if (hasattr(expr, 'function') and expr.function.func_type == 'scalar') or type(expr).__name__=='ColumnReferenceNode':
                                    # Record number of GROUP BY expressions before adding
                                    before_count = len(group_by.expressions)
                                    # Directly add expression to GROUP BY
                                    group_by.add_expression(expr)
                                    # Record number of GROUP BY expressions after adding
                                    after_count = len(group_by.expressions)
                            # Determine window function
                            if hasattr(expr, 'function') and expr.function.func_type == 'window':
                                if hasattr(expr,'metadata'):
                                    if expr.metadata.get('partition_by'):
                                        partition_by=expr.metadata.get('partition_by')
                                        before_count = len(group_by.expressions)
                                        
                                        # Try to get from_node (assuming it's available in parent scope)
                                        if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            
                                            for part_expr in partition_by:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                        
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                                
                                                except Exception as e:
                                                    print(f"  Error converting partition_by expression: {e}")
                                        
                                        after_count = len(group_by.expressions)
                                    if expr.metadata.get('order_by'):
                                        order_by=expr.metadata.get('order_by')
                                        before_count = len(group_by.expressions)
                                        for order in order_by:
                                            expr_parts = order.rsplit(' ', 1)
                                        if len(expr_parts) == 2 and expr_parts[1].upper() in ['ASC', 'DESC']:
                                            main_expr = expr_parts[0]
                                            main_expr = [main_expr]
                                            sort_direction = expr_parts[1]
                                        else:
                                            main_expr = order_by
                                            sort_direction = None
                                        # Try to get from_node (assuming it's available in parent scope)
                                        if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            
                                            for part_expr in main_expr:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                        
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                    else:
                                                        print(f"  Warning: Cannot parse order_by expression format: {part_expr}")
                                                except Exception as e:
                                                    print(f"  Error converting order_by expression: {e}")
                                        else:
                                            print("  Warning: from_node object is not available, cannot convert order_by to ColumnReferenceNode")
                                        
                                        after_count = len(group_by.expressions)
                            
                            # Check if select_node has order_by_clause attribute
                            if hasattr(select_node, 'order_by_clause') and select_node.order_by_clause:
                                # Iterate through expressions in order_by_clause
                                for expr, direction in select_node.order_by_clause.expressions:
                                    expr=[expr.to_sql()]
                                    # These expressions can be processed here as needed
                                    if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            for part_expr in expr:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                        
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                    else:
                                                        print(f"  Warning: Cannot parse order_by expression format: {part_expr}")
                                                except Exception as e:
                                                    print(f"  Error converting order_by expression: {e}")
                                    else:
                                            print("  Warning: from_node object is not available, cannot convert order_by to ColumnReferenceNode")
                                        
            # Set GROUP BY clause
            if group_by.expressions:
                select_node.group_by_clause = group_by
            # Add query to set operation
            set_op_node.add_query(select_node)
    
        # Return set operation SQL
        if use_cte:
            return f'{with_node.to_sql()} {set_op_node.to_sql()}'
        else:
            return set_op_node.to_sql()
    
    # Create SELECT node
    select_node = SelectNode()
    # Initialize column usage tracker
    column_tracker = ColumnUsageTracker()
    # Randomly set distinct attribute, 50% probability to use DISTINCT
    if random.random() < 0.5:
        select_node.distinct = True
    select_node.tables = tables
    select_node.functions = functions
    # Store column tracker for later use
    select_node.metadata = {'column_tracker': column_tracker}

    # Create FROM clause
    from_node = FromNode()
    sql_keywords = {'use', 'select', 'from', 'where', 'group', 'by', 'order', 'limit', 'join', 'on', 'as'}


    # Decide whether to use subquery based on current and maximum depth
    use_subquery = current_depth < SUBQUERY_DEPTH and random.random() > 0.3
    main_alias = ''
    main_table = None

    if use_subquery and len(tables) >= 1:
        # Create subquery as main table
        subquery_select = SelectNode()

        subquery_table = random.choice(tables)
        subquery_select.tables = [subquery_table]
        
        # Generate unique internal alias for subquery
        subquery_inner_alias = 's' + str(random.randint(100, 999))
        
        # Generate FROM clause for subquery
        subquery_from = FromNode()
        subquery_from.add_table(subquery_table, subquery_inner_alias)
        subquery_select.set_from_clause(subquery_from)
        
        # Generate SELECT expressions for subquery (may include aggregate functions)
        subquery_num_cols = random.randint(2, 4)  # Ensure at least 2 SELECT expressions
        subquery_non_aggregate = []
        subquery_has_aggregate = False
        
        for _ in range(subquery_num_cols):
            if random.random() > 0.4 and functions:
                # Add aggregate function
                agg_funcs = [f for f in functions if f.func_type == 'aggregate']
                if agg_funcs:
                    func = random.choice(agg_funcs)
                    func_node = FunctionCallNode(func)
                    
                    # Try to find matching type column as parameter
                    col_ref = None
                    expected_types = func.param_types if hasattr(func, 'param_types') else ['any']
                    expected_type = expected_types[0] if expected_types else 'any'
                    
                    # First try to find matching type columns
                    matching_columns = []
                    for col in subquery_table.columns:
                        if expected_type == 'any' or col.category == expected_type or \
                           (expected_type == 'numeric' and col.category in ['int', 'float', 'decimal']):
                            matching_columns.append(col)
                    
                    if matching_columns:
                        # Select unused column from matching type columns
                        available_matching_columns = []
                        for col_candidate in matching_columns:
                            if not column_tracker.is_column_used(subquery_inner_alias, col_candidate.name):
                                available_matching_columns.append(col_candidate)
                        
                        if available_matching_columns:
                            col = random.choice(available_matching_columns)
                            column_tracker.mark_column_as_used(subquery_inner_alias, col.name)
                        else:
                            # If no unused matching columns, select any matching column
                            col = random.choice(matching_columns)
                        
                        col_ref = ColumnReferenceNode(col, subquery_inner_alias)
                    else:
                        # If no matching type columns, still use a random column instead of a literal
                        col = get_random_column_with_tracker(subquery_table, subquery_inner_alias, column_tracker, for_select=True)
                        col_ref = ColumnReferenceNode(col, subquery_inner_alias)
                    
                    # Ensure successful addition, still use column reference if failed
                    added = func_node.add_child(col_ref)
                    if not added:
                        # Prioritize using column reference over literal even if types don't match
                        func_node.children.append(col_ref)
                    
                    subquery_select.add_select_expression(func_node, f'{func.name.lower()}_{col.name}')
                    subquery_has_aggregate = True
            else:
                # Add regular column
                col = get_random_column_with_tracker(subquery_table, subquery_inner_alias, column_tracker, for_select=True)
                col_ref = ColumnReferenceNode(col, subquery_inner_alias)
                subquery_select.add_select_expression(col_ref, col.name)
                subquery_non_aggregate.append(col_ref)

        # If there are aggregate functions, add GROUP BY clause
        if subquery_has_aggregate:
            if not subquery_non_aggregate:
                col = get_random_column_with_tracker(subquery_table, subquery_inner_alias, column_tracker, for_select=True)
                col_ref = ColumnReferenceNode(col, subquery_inner_alias)
                subquery_non_aggregate.append(col_ref)
            
            subquery_group_by = GroupByNode()
            for col_ref in subquery_non_aggregate:
                subquery_group_by.add_expression(col_ref)
            
            subquery_select.set_group_by_clause(subquery_group_by)

        # Generate WHERE clause for subquery (optional)
        if random.random() > 0.5:
            col = subquery_table.get_random_column()
            col_ref = ColumnReferenceNode(col, subquery_inner_alias)
            operator = random.choice(['=', '<>', '<', '>', '<=', '>=', 'IS NULL', 'IS NOT NULL'])
            comp_node = ComparisonNode(operator)
            comp_node.add_child(col_ref)
            
            if operator not in ['IS NULL', 'IS NOT NULL']:
                if col.category == 'numeric':
                    comp_node.add_child(LiteralNode(random.randint(0, 100), col.data_type))
                elif col.category == 'string':
                    comp_node.add_child(LiteralNode(f"'sample_{random.randint(1, 100)}'", col.data_type))
                elif col.category == 'datetime':
                    # Generate random datetime with hours, minutes, and seconds
                    hours = random.randint(0, 23)
                    minutes = random.randint(0, 59)
                    seconds = random.randint(0, 59)
                    datetime_str = f"'2023-01-01 {hours:02d}:{minutes:02d}:{seconds:02d}'"
                    comp_node.add_child(LiteralNode(datetime_str, col.data_type))
            
            subquery_select.set_where_clause(comp_node)

        # Randomly add ORDER BY clause
        if random.random() > 0.4:
            order_by = OrderByNode()
            if subquery_has_aggregate and subquery_non_aggregate:
                # If there is GROUP BY clause, select from GROUP BY columns
                col_ref = random.choice(subquery_non_aggregate)
            else:
                # Otherwise randomly select a column
                col = subquery_table.get_random_column()
                col_ref = ColumnReferenceNode(col, subquery_inner_alias)
            order_by.add_expression(col_ref, random.choice(['ASC', 'DESC']))
            subquery_select.set_order_by_clause(order_by)

        # Randomly add LIMIT clause
        if random.random() > 0.5:
            subquery_select.set_limit_clause(LimitNode(random.randint(1, 10)))

        # Generate alias for subquery
        base_sub_alias = 'subq'
        main_alias = base_sub_alias if base_sub_alias not in sql_keywords else 'sub' + str(random.randint(0, 9))

        # Create subquery node and add to FROM clause
        subquery_node = SubqueryNode(subquery_select, main_alias)
        from_node.add_table(subquery_node, main_alias)
        main_table = subquery_table  # Save main table info for later processing
        # Store current depth information
        subquery_node.metadata['depth'] = current_depth + 1
    else:
        main_table = random.choice(tables)
        base_alias = main_table.name[:3].lower()
        main_alias = base_alias if base_alias not in sql_keywords else main_table.name[:2].lower() + str(random.randint(0, 9))
        from_node.add_table(main_table, main_alias)

    # Randomly add join
    if random.random() > 0.3 and len(tables) > 1:
        join_table = random.choice([t for t in tables if t.name != main_table.name])
        # Generate safe join table alias to avoid SQL keyword conflicts
        base_join_alias = join_table.name[:3].lower()
        join_alias = base_join_alias if base_join_alias not in sql_keywords else join_table.name[:2].lower() + str(random.randint(0, 9))

        # Create join condition (with type checking and conversion)
        fk = next((fk for fk in join_table.foreign_keys if fk["ref_table"] == main_table.name), None)
        if fk:
            # Get actual column objects instead of creating new ones
            join_col = join_table.get_column(fk["column"])
            main_col = main_table.get_column(fk["ref_column"])
            
            if join_col and main_col:
                left_col_ref = ColumnReferenceNode(join_col, join_alias)
                right_col_ref = ColumnReferenceNode(main_col, main_alias)
                
                # Check if column types match
                if join_col.data_type.lower() != main_col.data_type.lower():
                    # Types don't match, perform explicit conversion
                    # All necessary classes are imported at the top of the file, no need for local imports
                    
                    # Determine conversion function (using CAST function as example here)
                    # In practice, you may need to choose the appropriate conversion function based on database dialect and specific types
                    if main_col.category == 'numeric' and join_col.category == 'string':
                        # String to numeric
                        cast_func = Function('CAST', 'numeric', ['any', 'string'])
                        cast_node = FunctionCallNode(cast_func)
                        cast_node.add_child(left_col_ref)
                        cast_node.add_child(LiteralNode(f'AS {main_col.data_type}', 'VARCHAR'))
                        left_col_ref = cast_node
                    elif main_col.category == 'string' and join_col.category == 'numeric':
                        # Numeric to string
                        cast_func = Function('CAST', 'string', ['any', 'string'])
                        cast_node = FunctionCallNode(cast_func)
                        cast_node.add_child(left_col_ref)
                        cast_node.add_child(LiteralNode(f'AS {main_col.data_type}', 'VARCHAR'))
                        left_col_ref = cast_node
                    elif main_col.category == 'datetime' and join_col.category == 'string':
                        # String to datetime
                        cast_func = Function('CAST', 'datetime', ['any', 'string'])
                        cast_node = FunctionCallNode(cast_func)
                        cast_node.add_child(left_col_ref)
                        cast_node.add_child(LiteralNode(f'AS {main_col.data_type}', 'VARCHAR'))
                        left_col_ref = cast_node
                    
                # Create join condition
                condition = ComparisonNode("=")
                condition.add_child(left_col_ref)
                condition.add_child(right_col_ref)
            else:
                # Fall back to original implementation if actual column objects cannot be obtained
                left_col = ColumnReferenceNode(
                    Column(fk["column"], "", "numeric", False, join_table.name),
                    join_alias
                )
                right_col = ColumnReferenceNode(
                    Column(fk["ref_column"], "", "numeric", False, main_table.name),
                    main_alias
                )
                condition = ComparisonNode("=")
                condition.add_child(left_col)
                condition.add_child(right_col)

            from_node.add_join(
                random.choice(["INNER", "LEFT", "RIGHT", "CROSS"]),
                join_table,
                join_alias,
                condition
            )

    select_node.set_from_clause(from_node)
        # Initialize available column information for column tracker
    if select_node.metadata and 'column_tracker' in select_node.metadata:
        column_tracker = select_node.metadata['column_tracker']
        if hasattr(column_tracker, 'initialize_from_from_node'):
            column_tracker.initialize_from_from_node(from_node)

    # Validate if all column reference aliases are valid
    valid, errors = select_node.validate_all_columns()
    if not valid:
        # If validation fails, repair invalid column references
        select_node.repair_invalid_columns()
        # Validate again
        valid, errors = select_node.validate_all_columns()
        
    # Add SELECT expressions
    num_columns = random.randint(1, 5)
    non_aggregate_columns = []  # Store non-aggregate columns
    has_aggregate_function = False  # Track if aggregate function is included
    used_aliases = set()  # Track used column aliases
    for _ in range(num_columns):
        if random.random() > 0.3 and functions:  # 30% probability to use function
            func = random.choice(functions)
            func_node = FunctionCallNode(func)

            # Add parameters to function
            # Ensure parameter count is within valid range
            param_count = func.min_params
            if func.max_params is not None and func.max_params > func.min_params:
                param_count = random.randint(func.min_params, func.max_params)
            else:
                param_count = func.min_params
            for param_idx in range(param_count):
                # Select appropriate column based on function parameter type
                expected_type = func.param_types[param_idx] if param_idx < len(func.param_types) else 'any'
                col_ref = None

                # Select columns based on main table type
                if use_subquery:
                    # Select from subquery column aliases
                    subquery_node = from_node.table_references[0]
                    if hasattr(subquery_node, 'column_alias_map'):
                        # Get column aliases from subquery
                        valid_aliases = list(subquery_node.column_alias_map.keys())
                        # Special handling for DATE_FORMAT and CONCAT functions
                        # Unified handling of special logic for three functions
                        # 1. Special handling for SUBSTRING function
                        if func.name == 'SUBSTRING':
                            # First parameter must be string type
                            if param_idx == 0:
                                # Prioritize string type column aliases
                                string_aliases = []
                                for alias in valid_aliases:
                                    _, _, category = subquery_node.column_alias_map[alias]
                                    if category == 'string':
                                        string_aliases.append(alias)
                                
                                if string_aliases:
                                    alias = random.choice(string_aliases)
                                    col_name, data_type, category = subquery_node.column_alias_map[alias]
                                    col = Column(col_name, main_table.name, data_type, False, main_table.name)
                                    col_ref = ColumnReferenceNode(col, main_alias)
                                else:
                                    # No string type columns available, using literal string
                                    col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                            # Second and third parameters must be numeric (position and length)
                            elif param_idx in [1, 2]:
                                # Generate a reasonable integer value
                                value = random.randint(1, 20) if param_idx == 1 else random.randint(1, 10)
                                col_ref = LiteralNode(value, 'INT')
                        
                        # 2. Special handling for DATE_FORMAT/TO_CHAR functions
                        elif (func.name == 'DATE_FORMAT' or func.name == 'TO_CHAR'):
                            # First parameter: datetime column
                            if param_idx == 0:
                                # Fix: Use correct table and alias selection (ensure table is in FROM clause)
                                # Initialize tables_to_choose_with_aliases variable
                                available_tables = []
                                for ref in from_node.table_references:
                                    if isinstance(ref, Table):
                                        available_tables.append((ref, from_node.get_alias_for_table(ref)))
                                    elif isinstance(ref, SubqueryNode):
                                        available_tables.append((ref, ref.alias))
                                
                                tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
                                
                                if tables_to_choose_with_aliases:
                                    table, alias = random.choice(tables_to_choose_with_aliases)
                                    # Find date type columns
                                    date_columns = [col for col in table.columns if col.category == 'datetime']
                                    if date_columns:
                                        # Get column tracker
                                        column_tracker = select_node.metadata.get('column_tracker')
                                        if column_tracker:
                                            # Filter out unused date columns
                                            available_date_columns = []
                                            for col in date_columns:
                                                col_identifier = f"{alias}.{col.name}"
                                                if not column_tracker.is_column_used(col_identifier):
                                                    available_date_columns.append(col)
                                            
                                            if available_date_columns:
                                                col = random.choice(available_date_columns)
                                                # Mark column as used
                                                col_identifier = f"{alias}.{col.name}"
                                                column_tracker.mark_column_used(col_identifier)
                                            else:
                                                col = random.choice(date_columns)
                                        else:
                                            col = random.choice(date_columns)
                                        col_ref = ColumnReferenceNode(col, alias)
                                    else:
                                        # Fix: When there are no date type columns, use date literal
                                        col_ref = LiteralNode('2023-01-01', 'DATE')
                                else:
                                    # No available tables, using date literal
                                    col_ref = LiteralNode('2023-01-01', 'DATE')
                            # Second parameter: format string literal
                            elif param_idx == 1 and hasattr(func, 'format_string_required') and func.format_string_required:
                                # Second parameter is format string
                                # Select correct format based on function type
                                if func.name == 'TO_CHAR':
                                    # PostgreSQL TO_CHAR format
                                    format_strings = ['YYYY-MM-DD', 'YYYY-MM-DD HH24:MI:SS', 'DD-MON-YYYY', 'HH24:MI:SS']
                                else:
                                    # MySQL DATE_FORMAT format
                                    format_strings = ['%Y-%m-%d', '%Y-%m-%d %H:%i:%s', '%d-%b-%Y', '%H:%i:%s']
                                # Use STRING type to ensure quotes are added correctly
                                col_ref = LiteralNode(random.choice(format_strings), 'STRING')
                        
                        # 3. Special handling for CONCAT function: ensure all parameters are string type
                        elif func.name == 'CONCAT' and expected_type == 'string':
                            # Prioritize string type columns aliases
                            string_aliases = []
                            for alias in valid_aliases:
                                _, _, category = subquery_node.column_alias_map[alias]
                                if category == 'string':
                                    string_aliases.append(alias)
                            
                            if string_aliases:
                                alias = random.choice(string_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(col_name, main_table.name, data_type, False, main_table.name)
                                col_ref = ColumnReferenceNode(col, main_alias)
                            else:
                                # No string type columns available, using literal string
                                col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                        elif valid_aliases:
                            # Try to find matching type columns
                            matching_aliases = []
                            for alias in valid_aliases:
                                _, data_type, category = subquery_node.column_alias_map[alias]
                                if expected_type == 'any' or category == expected_type or (expected_type == 'numeric' and category in ['int', 'float', 'decimal']):
                                    matching_aliases.append(alias)

                            if matching_aliases:
                                alias = random.choice(matching_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                # Create column reference that refers to subquery column alias
                                col = Column(col_name, main_table.name, data_type, False, main_table.name)
                                col_ref = ColumnReferenceNode(col, main_alias)
                            else:
                                # No matching type columns, use fallback solution
                                alias = random.choice(valid_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(col_name, main_table.name, data_type, False, main_table.name)
                                col_ref = ColumnReferenceNode(col, main_alias)
                    else:
                        # Fallback solution
                        col = main_table.get_random_column()
                        col_ref = ColumnReferenceNode(col, main_alias)
                else:
                        # Select columns from regular tables
                        # Fix: Only use tables actually included in FROM clause
                        available_tables = []
                        for ref in from_node.table_references:
                            if isinstance(ref, Table):
                                available_tables.append((ref, from_node.get_alias_for_table(ref)))
                            elif isinstance(ref, SubqueryNode):
                                available_tables.append((ref, ref.alias))
                        
                        tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
                        # Keep table and alias correspondence, don't create separate tables_to_choose variable
                        
                        # Special handling for DATE_FORMAT and CONCAT functions
                        # Unified handling of special logic for three functions
                        # 1. Special handling for SUBSTRING function
                        if func.name == 'SUBSTRING':
                            # First parameter must be string type
                            if param_idx == 0:
                                # Prioritize string type columns
                                string_columns = []
                                for table, alias in tables_to_choose_with_aliases:
                                    for col in table.columns:
                                        if col.category == 'string':
                                            string_columns.append((table, col, alias))
                                
                                # Get column tracker
                                column_tracker = select_node.metadata.get('column_tracker')
                                
                                if string_columns:
                                    # Use column tracker to select unused columns
                                    if column_tracker:
                                        # Filter out unused columns
                                        available_string_columns = []
                                        for table, col, alias in string_columns:
                                            col_identifier = f"{alias}.{col.name}"
                                            if not column_tracker.is_column_used(col_identifier):
                                                available_string_columns.append((table, col, alias))
                                        
                                        if available_string_columns:
                                            table, col, alias = random.choice(available_string_columns)
                                            # Mark column as used
                                            col_identifier = f"{alias}.{col.name}"
                                            column_tracker.mark_column_used(col_identifier)
                                            col_ref = ColumnReferenceNode(col, alias)
                                        else:
                                            # If no unused columns, fall back to random selection
                                            table, col, alias = random.choice(string_columns)
                                            col_ref = ColumnReferenceNode(col, alias)
                                    else:
                                        table, col, alias = random.choice(string_columns)
                                        col_ref = ColumnReferenceNode(col, alias)
                                else:
                                    # No string type columns available, using literal string
                                    col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                            # Second and third parameters must be numeric (position and length)
                            elif param_idx in [1, 2]:
                                # Generate a reasonable integer value
                                value = random.randint(1, 20) if param_idx == 1 else random.randint(1, 10)
                                col_ref = LiteralNode(value, 'INT')
                        
                        # 2. Special handling for DATE_FORMAT/TO_CHAR functions
                        elif (func.name == 'DATE_FORMAT' or func.name == 'TO_CHAR'):
                            # First parameter: datetime column
                            if param_idx == 0:
                                # Prioritize datetime type columns
                                datetime_columns = []
                                for table, alias in tables_to_choose_with_aliases:
                                    for col in table.columns:
                                        if col.category == 'datetime':
                                            datetime_columns.append((table, col, alias))
                                
                                # Get column tracker
                                column_tracker = select_node.metadata.get('column_tracker')
                                
                                if datetime_columns:
                                    # Use column tracker to select unused columns
                                    if column_tracker:
                                        # Filter out unused columns
                                        available_datetime_columns = []
                                        for table, col, alias in datetime_columns:
                                            col_identifier = f"{alias}.{col.name}"
                                            if not column_tracker.is_column_used(col_identifier):
                                                available_datetime_columns.append((table, col, alias))
                                        
                                        if available_datetime_columns:
                                            table, col, alias = random.choice(available_datetime_columns)
                                            # Mark column as used
                                            col_identifier = f"{alias}.{col.name}"
                                            column_tracker.mark_column_used(col_identifier)
                                            col_ref = ColumnReferenceNode(col, alias)
                                        else:
                                            # If no unused columns, fall back to random selection
                                            table, col, alias = random.choice(datetime_columns)
                                            col_ref = ColumnReferenceNode(col, alias)
                                    else:
                                        table, col, alias = random.choice(datetime_columns)
                                        col_ref = ColumnReferenceNode(col, alias)
                                else:
                                    # No datetime type columns, use date literal
                                    col_ref = LiteralNode('2023-01-01', 'DATE')
                            # Second parameter: format string literal
                            elif param_idx == 1 and hasattr(func, 'format_string_required') and func.format_string_required:
                                # Provide valid date format strings for DATE_FORMAT and TO_CHAR functions
                                # MySQL's DATE_FORMAT uses percent format
                                # PostgreSQL's TO_CHAR uses format without percent signs
                                if func.name == 'TO_CHAR':
                                    # PostgreSQL TO_CHAR format
                                    format_strings = ['YYYY-MM-DD', 'YYYY-MM-DD HH24:MI:SS', 'DD-MON-YYYY', 'HH24:MI:SS']
                                else:
                                    # MySQL DATE_FORMAT format
                                    format_strings = ['%Y-%m-%d', '%Y-%m-%d %H:%i:%s', '%d-%b-%Y', '%H:%i:%s']
                                # Use STRING type to ensure quotes are added correctly, don't add single quotes directly in values
                                col_ref = LiteralNode(random.choice(format_strings), 'STRING')
                        
                        # 3. Special handling for CONCAT function: ensure all parameters are string type
                        elif func.name == 'CONCAT' and expected_type == 'string':
                            # Prioritize string type columns
                            string_columns = []
                            for table, alias in tables_to_choose_with_aliases:
                                for col in table.columns:
                                    if col.category == 'string':
                                        string_columns.append((table, col, alias))
                            
                            # Get column tracker
                            column_tracker = select_node.metadata.get('column_tracker')
                            
                            if string_columns:
                                # Use column tracker to select unused columns
                                if column_tracker:
                                    # Filter out unused columns
                                    available_string_columns = []
                                    for table, col, alias in string_columns:
                                        col_identifier = f"{alias}.{col.name}"
                                        if not column_tracker.is_column_used(col_identifier):
                                            available_string_columns.append((table, col, alias))
                                    
                                    if available_string_columns:
                                        table, col, alias = random.choice(available_string_columns)
                                        # Mark column as used
                                        column_tracker.mark_column_as_used(alias, col.name)
                                        col_ref = ColumnReferenceNode(col, alias)
                                    else:
                                        # If no unused columns, fall back to random selection
                                        table, col, alias = random.choice(string_columns)
                                        col_ref = ColumnReferenceNode(col, alias)
                                else:
                                    table, col, alias = random.choice(string_columns)
                                    col_ref = ColumnReferenceNode(col, alias)
                            else:
                                # No string type columns, use literal string
                                col_ref = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                        else:
                            # Try to find matching type columns
                            matching_columns = []
                            for table, alias in tables_to_choose_with_aliases:
                                for col in table.columns:
                                    if expected_type == 'any' or col.category == expected_type or (expected_type == 'numeric' and col.category in ['int', 'float', 'decimal']):
                                        matching_columns.append((table, col, alias))

                            # Get column tracker
                            column_tracker = select_node.metadata.get('column_tracker')

                            if matching_columns:
                                # Use column tracker to select unused columns
                                if column_tracker:
                                    # Filter out unused columns
                                    available_matching_columns = []
                                    for table, col, alias in matching_columns:
                                        col_identifier = f"{alias}.{col.name}"
                                        if not column_tracker.is_column_used(col_identifier):
                                            available_matching_columns.append((table, col, alias))
                                    
                                    if available_matching_columns:
                                        table, col, alias = random.choice(available_matching_columns)
                                        # Mark column as used
                                        column_tracker.mark_column_as_used(alias, col.name)
                                        col_ref = ColumnReferenceNode(col, alias)
                                    else:
                                        # If no unused columns, fall back to random selection
                                        table, col, alias = random.choice(matching_columns)
                                        col_ref = ColumnReferenceNode(col, alias)
                                else:
                                    table, col, alias = random.choice(matching_columns)
                                    col_ref = ColumnReferenceNode(col, alias)
                            else:
                                # No matching type columns, use fallback solution
                                table, alias = random.choice(tables_to_choose_with_aliases)
                                col = table.get_random_column()
                                col_ref = ColumnReferenceNode(col, alias)
                # Ensure aggregate function always has parameters
                if func.func_type == 'aggregate':
                    # Enhanced parameter guarantee mechanism
                    if not col_ref:
                        if use_subquery:
                            subquery_node = from_node.table_references[0]
                            if hasattr(subquery_node, 'column_alias_map') and subquery_node.column_alias_map:
                                alias = random.choice(list(subquery_node.column_alias_map.keys()))
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(col_name, main_table.name, data_type, False, main_table.name)
                                col_ref = ColumnReferenceNode(col, main_alias)
                            else:
                                # Subquery has no column alias map, directly use main table columns
                                col = main_table.get_random_column()
                                col_ref = ColumnReferenceNode(col, main_alias)
                        else:
                            # Select columns from available tables
                            if tables_to_choose:
                                # Ensure selected table and alias are valid in FROM clause
                                valid_table_found = False
                                while not valid_table_found and tables_to_choose:
                                    table = random.choice(tables_to_choose)
                                    alias = main_alias if table == main_table else join_alias
                                    # Verify if the alias is defined in the FROM clause
                                    if from_node.get_table_for_alias(alias):
                                        col = table.get_random_column()
                                        col_ref = ColumnReferenceNode(col, alias)
                                        valid_table_found = True
                                    else:
                                        # If the alias is invalid, remove the table from the selection list
                                        tables_to_choose.remove(table)
                                # If all tables are invalid, use the main table
                                if not valid_table_found and main_table:
                                    alias = main_alias
                                    col = main_table.get_random_column()
                                    col_ref = ColumnReferenceNode(col, alias)
                            else:
                                # No available tables, creating literal parameters
                                if expected_type == 'numeric':
                                    col_ref = LiteralNode(random.randint(1, 100), 'INT')
                                elif expected_type == 'string':
                                    col_ref = LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
                                elif expected_type == 'datetime':
                                    col_ref = LiteralNode('2023-01-01', 'DATE')
                                else:
                                    # Default to numeric type
                                    col_ref = LiteralNode(random.randint(1, 100), 'INT')
                    # Ultimate safeguard: If all column reference schemes fail, use literals directly
                    if not col_ref:
                        if expected_type == 'numeric':
                            col_ref = LiteralNode(random.randint(1, 100), 'INT')
                        elif expected_type == 'string':
                            col_ref = LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
                        elif expected_type == 'datetime':
                            col_ref = LiteralNode('2023-01-01', 'DATE')
                        else:
                            # Default to numeric type
                            col_ref = LiteralNode(random.randint(1, 100), 'INT')

                # Add parameters and check type matching
                added = func_node.add_child(col_ref)
                if not added:
                    # If type doesn't match, try to find other columns with matching type
                    found_matching_column = False
                    # Try to find columns with matching type from available tables
                    for _ in range(3):  # Try 3 times to find columns with matching type
                        try:
                            # Select from available tables
                            tables_to_try = available_tables if available_tables else [(main_table, main_alias)]
                            table, alias = random.choice(tables_to_try)
                            # Get columns that match expected type
                            matching_cols = []
                            for c in table.columns:
                                # Simple type matching logic
                                if (expected_type == 'numeric' and c.category == 'numeric') or \
                                   (expected_type == 'string' and c.category == 'string') or \
                                   (expected_type == 'datetime' and c.category in ['datetime', 'date', 'timestamp']) or \
                                   expected_type == 'any':
                                    matching_cols.append(c)
                            
                            if matching_cols:
                                matched_col = random.choice(matching_cols)
                                matched_col_ref = ColumnReferenceNode(matched_col, alias)
                                # Try again to add columns with matching type
                                if func_node.add_child(matched_col_ref):
                                    found_matching_column = True
                                    break
                        except Exception:
                            # If error occurs during search, continue trying
                            continue
                    
                    # Use literals only if no columns with matching type are found
                    if not found_matching_column:
                        if expected_type == 'numeric':
                            literal = LiteralNode(random.randint(1, 100), 'INT')
                            func_node.add_child(literal)
                        elif expected_type == 'string':
                            literal = LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
                            func_node.add_child(literal)
                        elif expected_type == 'datetime':
                            literal = LiteralNode('2023-01-01', 'DATE')
                            func_node.add_child(literal)
                        else:
                            # Final fallback: use original column reference
                            func_node.children.append(col_ref)

                # Special safeguard: ensure function has sufficient parameters
                # Handle CONCAT function
                if func.name == 'CONCAT' and len(func_node.children) < func.min_params:
                    # Add missing string literal parameters
                    while len(func_node.children) < func.min_params:
                        literal = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                        func_node.add_child(literal)

                # If function is not an aggregate function, add column reference to non-aggregate column list
                if func.func_type != 'aggregate':
                    if col_ref is not None:
                        non_aggregate_columns.append(col_ref)

            # Use external used_aliases set for deduplication
            counter = 1
            base_alias = f"col_{counter}"
            current_alias = base_alias
            counter += 1
            # Add loop protection to avoid infinite loop
            max_attempts = 1000
            attempts = 0
            while current_alias in used_aliases and attempts < max_attempts:
                current_alias = f"{base_alias}_{counter}"
                counter += 1
                attempts += 1
            # If maximum attempts reached, use random string as alias
            if attempts >= max_attempts:
                current_alias = f"{base_alias}_{random.randint(1000, 9999)}"
            used_aliases.add(current_alias)
            # Set flag if aggregate function is added, regardless of parameter type
            select_node.add_select_expression(func_node, current_alias)
            if func.func_type == 'aggregate':
                has_aggregate_function = True
            # Ensure flag is set correctly when aggregate function parameter is subquery column
            if func.func_type == 'aggregate' and use_subquery:
                has_aggregate_function = True
        else:  # Otherwise use simple column
            # Select column based on main table type
            # Get column tracker
            column_tracker = select_node.metadata.get('column_tracker')
            
            if use_subquery:
                # Select from column aliases of subquery
                subquery_node = from_node.table_references[0]
                if hasattr(subquery_node, 'column_alias_map'):
                    # Get column aliases from subquery
                    valid_aliases = list(subquery_node.column_alias_map.keys())
                    if valid_aliases:
                        # Use column tracker to select unused columns
                        if column_tracker:
                            # Filter out unused columns
                            available_aliases = []
                            for alias in valid_aliases:
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col_identifier = f"{main_alias}.{alias}"
                                if not column_tracker.is_column_used(col_identifier):
                                    available_aliases.append(alias)
                            
                            if available_aliases:
                                alias = random.choice(available_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                # Create column reference that refers to subquery column alias
                                col = Column(col_name, 'subquery', data_type, False, main_alias)
                                # Mark column as used
                                col_identifier = f"{main_alias}.{alias}"
                                column_tracker.mark_column_as_filter(main_alias, alias)
                                col_ref = ColumnReferenceNode(col, main_alias)
                            else:
                                # If no unused columns, fallback to random selection
                                alias = random.choice(valid_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(col_name, 'subquery', data_type, False, main_alias)
                                col_ref = ColumnReferenceNode(col, main_alias)
                        else:
                            alias = random.choice(valid_aliases)
                            col_name, data_type, category = subquery_node.column_alias_map[alias]
                            col = Column(col_name, 'subquery', data_type, False, main_alias)
                            col_ref = ColumnReferenceNode(col, main_alias)
                    else:
                        # Fallback solution
                        col = main_table.get_random_column()
                        col_ref = ColumnReferenceNode(col, main_alias)
                else:
                    # Fallback solution
                    col = main_table.get_random_column()
                    col_ref = ColumnReferenceNode(col, main_alias)
            else:
                # Select column from regular table
                # Fix: only use tables actually included in FROM clause
                available_tables = []
                for ref in from_node.table_references:
                    if isinstance(ref, Table):
                        available_tables.append((ref, from_node.get_alias_for_table(ref)))
                    elif isinstance(ref, SubqueryNode):
                        available_tables.append((ref, ref.alias))
                
                tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
                
                # Use column tracker to select unused columns
                if column_tracker:
                    # Filter out unused columns
                    available_columns = []
                    for table, alias in tables_to_choose_with_aliases:
                        for col in table.columns:
                            col_identifier = f"{alias}.{col.name}"
                            if not column_tracker.is_column_used(col_identifier):
                                available_columns.append((table, col, alias))
                    
                    if available_columns:
                        table, col, alias = random.choice(available_columns)
                        # Mark column as used
                        col_identifier = f"{alias}.{col.name}"
                        column_tracker.mark_column_used(col_identifier)
                        col_ref = ColumnReferenceNode(col, alias)
                    else:
                        # If no unused columns, fall back to random selection
                        table, alias = random.choice(tables_to_choose_with_aliases)
                        col = table.get_random_column()
                        col_ref = ColumnReferenceNode(col, alias)
                else:
                    table, alias = random.choice(tables_to_choose_with_aliases)
                    col = table.get_random_column()
                    col_ref = ColumnReferenceNode(col, alias)
            # Add alias deduplication logic for simple columns
            base_alias = col.name
            current_alias = base_alias
            counter = 1
            while current_alias in used_aliases:
                current_alias = f"{base_alias}_{counter}"
                counter += 1
            used_aliases.add(current_alias)
            select_node.add_select_expression(col_ref, current_alias)
            if col_ref is not None:
                non_aggregate_columns.append(col_ref)

    # Randomly add WHERE clause,
    # Ensure col_ref is always defined
    if use_subquery:
            # Select from subquery column aliases
            subquery_node = from_node.table_references[0]
            if hasattr(subquery_node, 'column_alias_map'):
                # Get column aliases from subquery
                valid_aliases = list(subquery_node.column_alias_map.keys())
                if valid_aliases:
                    alias = random.choice(valid_aliases)
                    col_name, data_type, category = subquery_node.column_alias_map[alias]
                    # Create column reference that refers to subquery column alias
                    # Create subquery column reference with correct column name and alias
                    col = Column(col_name, 'subquery', data_type, False, main_alias)
                    col_ref = ColumnReferenceNode(col, main_alias)
                else:
                    # Fallback solution
                    # Select from tables actually included in the FROM clause
                    available_tables = []
                    for ref in from_node.table_references:
                        if isinstance(ref, Table):
                            available_tables.append((ref, from_node.get_alias_for_table(ref)))
                        elif isinstance(ref, SubqueryNode):
                            available_tables.append((ref, ref.alias))
                    
                    tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
                    
                    table, alias = random.choice(tables_to_choose_with_aliases)
                    col = table.get_random_column()
                    col_ref = ColumnReferenceNode(col, alias)
            else:
                # Fallback solution
                # Select from tables actually included in the FROM clause
                available_tables = []
                for ref in from_node.table_references:
                    if isinstance(ref, Table):
                        available_tables.append((ref, from_node.get_alias_for_table(ref)))
                    elif isinstance(ref, SubqueryNode):
                        available_tables.append((ref, ref.alias))
                
                tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
                
                table, alias = random.choice(tables_to_choose_with_aliases)
                col = table.get_random_column()
                col_ref = ColumnReferenceNode(col, alias)
    else:
            # Select from tables actually included in the FROM clause
            available_tables = []
            for ref in from_node.table_references:
                if isinstance(ref, Table):
                    available_tables.append((ref, from_node.get_alias_for_table(ref)))
                elif isinstance(ref, SubqueryNode):
                    available_tables.append((ref, ref.alias))
            
            tables_to_choose_with_aliases = available_tables if available_tables else [(main_table, main_alias)]
            
            table, alias = random.choice(tables_to_choose_with_aliases)
            col = table.get_random_column()
            col_ref = ColumnReferenceNode(col, alias)
    # Add WHERE clause only when random.random() > 0.2
    if random.random() > 0.2:
        # Use create_where_condition function to generate WHERE conditions, supporting rich conditions like subqueries
        where_node = create_where_condition(
            tables, functions, from_node, main_table, main_alias,
            join_table if 'join_table' in locals() else None, 
            join_alias if 'join_alias' in locals() else None,
            use_subquery=use_subquery,
            column_tracker=select_node.metadata.get('column_tracker')
        )
        select_node.set_where_clause(where_node)

    # Add GROUP BY clause if aggregate function exists
    # Use unified flag has_aggregate_function to trigger GROUP BY generation
    if has_aggregate_function:
        group_by = GroupByNode()
        # Initialize set of added columns
        added_columns = set()
        # Additionally add 0-1 random grouping columns (only from tables in query and output columns of subqueries)
        additional_groups = random.randint(0, 1)
        available_columns = []

        # For subqueries, only add columns output by subquery (column aliases)
        if use_subquery and hasattr(from_node.table_references[0], 'column_alias_map'):
            subquery_node = from_node.table_references[0]
            for alias, (col_name, data_type, category) in subquery_node.column_alias_map.items():
                # Create virtual columns representing subquery output columns, using alias as column name
                # Create subquery column reference with correct column name and alias
                col = Column(alias, 'subquery', data_type, False, main_alias)
                available_columns.append((col, main_alias))
        else:
            # Add columns from main table and joined tables
            if 'main_table' in locals():
                for col in main_table.columns:
                    available_columns.append((col, main_alias))
            if 'join_table' in locals():
                for col in join_table.columns:
                    available_columns.append((col, join_alias))
        


       
        # Add HAVING clause
        if random.random() > 0.5:
            # Select an aggregate function
            agg_funcs = [f for f in functions if f.func_type == "aggregate"]
            if agg_funcs:
                agg_func = random.choice(agg_funcs)
                func_node = FunctionCallNode(agg_func)
                # Select appropriate column for aggregate function
                # Get column tracker
                column_tracker = select_node.metadata.get('column_tracker')
                
                # Select appropriate column based on query type
                if use_subquery and hasattr(from_node.table_references[0], 'column_alias_map'):
                    subquery_node = from_node.table_references[0]
                    # Select from output columns of subquery
                    valid_aliases = list(subquery_node.column_alias_map.keys())
                    if valid_aliases:
                        # Use column tracker to select unused columns
                        if column_tracker:
                            # Filter out unused columns
                            available_aliases = []
                            for alias in valid_aliases:
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(alias, 'subquery', data_type, False, main_alias)
                                col_identifier = f"{main_alias}.{alias}"
                                if not column_tracker.is_column_used(col_identifier):
                                    available_aliases.append(alias)
                            
                            if available_aliases:
                                alias = random.choice(available_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(alias, 'subquery', data_type, False, main_alias)
                                # Mark column as used
                                col_identifier = f"{main_alias}.{alias}"
                                column_tracker.mark_column_as_filter(main_alias,alias)
                                func_node.add_child(ColumnReferenceNode(col, main_alias))
                            else:
                                # If no unused columns, fall back to random selection
                                alias = random.choice(valid_aliases)
                                col_name, data_type, category = subquery_node.column_alias_map[alias]
                                col = Column(alias, 'subquery', data_type, False, main_alias)
                                func_node.add_child(ColumnReferenceNode(col, main_alias))
                        else:
                            alias = random.choice(valid_aliases)
                            col_name, data_type, category = subquery_node.column_alias_map[alias]
                            col = Column(alias, 'subquery', data_type, False, main_alias)
                            func_node.add_child(ColumnReferenceNode(col, main_alias))
                    else:
                        # Fallback solution
                        col = main_table.get_random_column()
                        func_node.add_child(ColumnReferenceNode(col, main_alias))
                else:
                    # Select column from main table
                    # Default initialize valid_columns variable to ensure it always has a value
                    valid_columns = main_table.columns
                    
                    if agg_func.name in ['SUM', 'AVG']:
                        valid_columns = [col for col in main_table.columns if col.category == 'numeric']
                    elif agg_func.name in ['MAX', 'MIN']:
                        valid_columns = [col for col in main_table.columns if col.category in ['numeric', 'datetime', 'string']]
                    elif agg_func.name == 'COUNT':
                        valid_columns = main_table.columns
                    else:
                        valid_columns = [col for col in main_table.columns if col.category == 'numeric']
                    
                    # Use column tracker to select unused columns
                    if column_tracker:
                        # Filter out unused columns
                        available_columns = []
                        for col in valid_columns:
                            col_identifier = f"{main_alias}.{col.name}"
                            if not column_tracker.is_column_used(col_identifier):
                                available_columns.append(col)
                        
                        if available_columns:
                            col = random.choice(available_columns)
                            # Mark column as used
                            col_identifier = f"{main_alias}.{col.name}"
                            column_tracker.mark_column_as_filter(main_alias, col.name)
                            func_node.add_child(ColumnReferenceNode(col, main_alias))
                        else:
                            # If no unused columns, fall back to random selection
                            if valid_columns:
                                col = random.choice(valid_columns)
                            else:
                                col = main_table.get_random_column()
                            func_node.add_child(ColumnReferenceNode(col, main_alias))
                    else:
                        # Ensure at least one valid column exists
                        if not valid_columns:
                            valid_columns = main_table.columns
                if valid_columns:
                    col = random.choice(valid_columns)
                else:
                    col = main_table.get_random_column()
                func_node.add_child(ColumnReferenceNode(col, main_alias))

            # Select comparison operator
            operator = random.choice(['>', '>=', '<', '<=', '=', '<>'])
            having_node = ComparisonNode(operator)
            having_node.add_child(func_node)
            
            # Create literal compatible with aggregate function return type
            # Aggregate functions typically return numeric types
            literal_value = random.randint(0, 10)
            literal_type = "INT"
            
            # Special handling for different aggregate function return types
            if agg_func.name == 'COUNT':
                # COUNT typically returns integer
                literal_value = random.randint(0, 5)
            elif agg_func.name in ['AVG', 'SUM']:
                # AVG and SUM may return floating points
                literal_type = "DECIMAL"
                literal_value = round(random.uniform(0, 10), 2)
            
            having_node.add_child(LiteralNode(literal_value, literal_type))

            select_node.set_having_clause(having_node)

    # Randomly add ORDER BY clause
    if random.random() > 0.4:
        order_by = OrderByNode()
        # Check if there's a GROUP BY clause
        if has_aggregate_function and select_node.group_by_clause:
            # With GROUP BY clause, can only select GROUP BY columns or aggregate functions
            valid_order_columns = []
            
            # Add GROUP BY columns
            if hasattr(select_node.group_by_clause, 'expressions'):
                valid_order_columns.extend(select_node.group_by_clause.expressions)
            
            # Add aggregate functions
            for expr, _ in select_node.select_expressions:
                if hasattr(expr, 'metadata') and expr.metadata.get('is_aggregate', False):
                    valid_order_columns.append(expr)
            
            # Ensure at least one valid ORDER BY column exists
            if valid_order_columns:
                # Select from valid columns
                expr = random.choice(valid_order_columns)
                order_by.add_expression(expr, random.choice(["ASC", "DESC"]))
                select_node.set_order_by_clause(order_by)
        elif not (has_aggregate_function and select_node.group_by_clause) and use_subquery and hasattr(from_node.table_references[0], 'column_alias_map'):
            # Select from subquery column aliases
            subquery_node = from_node.table_references[0]
            valid_aliases = list(subquery_node.column_alias_map.keys())
            if valid_aliases:
                alias = random.choice(valid_aliases)
                col_name, data_type, category = subquery_node.column_alias_map[alias]
                # Create subquery column reference with correct column name and alias
                col = Column(alias, 'subquery', data_type, False, main_alias)
                expr = ColumnReferenceNode(col, main_alias)
                
                # Check if DISTINCT is used, if so, ensure ORDER BY column is in SELECT list
                if select_node.distinct:
                    # Check if already in SELECT list
                    in_select = False
                    for selected_expr, selected_alias in select_node.select_expressions:
                        if hasattr(selected_expr, 'to_sql') and selected_expr.to_sql() == expr.to_sql():
                            in_select = True
                            break
                    
                    # If not in SELECT list, add it
                    if not in_select:
                        select_node.add_select_expression(expr, alias)
                
                order_by.add_expression(expr, random.choice(["ASC", "DESC"]))
                select_node.set_order_by_clause(order_by)
        elif not (has_aggregate_function and select_node.group_by_clause):
            # Select column from main table
            col = main_table.get_random_column()
            expr = ColumnReferenceNode(col, main_alias)
            
            # Check if DISTINCT is used, if so, ensure ORDER BY columns are in SELECT list
            if select_node.distinct:
                # Check if already in SELECT list
                in_select = False
                for selected_expr, selected_alias in select_node.select_expressions:
                    if hasattr(selected_expr, 'to_sql') and selected_expr.to_sql() == expr.to_sql():
                        in_select = True
                        break
                
                # If not in SELECT list, add it
                if not in_select:
                    select_node.add_select_expression(expr, col.name)
            
            order_by.add_expression(expr, random.choice(["ASC", "DESC"]))
            select_node.set_order_by_clause(order_by)

    # Randomly add LIMIT clause
    if random.random() > 0.5 and not has_aggregate_function:
        select_node.set_limit_clause(LimitNode(random.randint(1, 10)))

    # Randomly add locking clause (about 30% probability)
    if random.random() > 0.7:
        # Randomly select a locking mode
        lock_modes = ['update', 'share', 'no key update', 'key share']
        selected_mode = random.choice(lock_modes)
        select_node.set_for_update(selected_mode)

    # Validate and repair SQL
    valid, errors = select_node.validate_all_columns()
    if not valid:
        select_node.repair_invalid_columns()
    # Simplified column reference handling function - removed column name validity check
    def validate_column_references(node, available_tables, used_aliases):
        # Recursively check child nodes
        for child in getattr(node, 'children', []):
            validate_column_references(child, available_tables, used_aliases)

        # Check if current node is a column reference
        if hasattr(node, 'table_alias') and hasattr(node, 'column'):
            # Only keep table alias existence check, remove column name validity check
            table_alias = node.table_alias
            if table_alias not in available_tables and available_tables:
                # Try to fix: use first available table alias
                new_alias = list(available_tables.keys())[0]
                node.table_alias = new_alias

    # Check and fix all function parameters in SQL
    def fix_all_function_params(select_node, main_table, join_table=None, main_alias=None, join_alias=None):
    # Build available tables dictionary
        available_tables = {}
        if main_table and main_alias:
            available_tables[main_alias] = main_table
        if join_table and join_alias:
            available_tables[join_alias] = join_table

        # Validate and repair column references (including ON clause)
        validate_column_references(select_node, available_tables, {})

        # Special check and repair for column references in ON clause
        if hasattr(select_node, 'from_clause') and hasattr(select_node.from_clause, 'joins'):
            for join in select_node.from_clause.joins:
                condition = join.get('condition')
                if condition:
                    validate_column_references(condition, available_tables, {})

        # Removed duplicate column alias check logic

    # Ensure GROUP BY clause contains all non-aggregate columns in SELECT - comply with only_full_group_by mode
    # If there are aggregate functions, must comply with only_full_group_by mode
    # Use unified has_aggregate_function flag
    if has_aggregate_function:
        # Check if SELECT list only contains aggregate functions
        all_agg = all(hasattr(expr, 'function') and expr.function.func_type == 'aggregate' for expr, _ in select_node.select_expressions)
        
        if not all_agg:
            # Case 1: Has aggregate functions and non-aggregate columns - must have GROUP BY clause
            if not hasattr(select_node, 'group_by_clause') or not select_node.group_by_clause:
                # Create GROUP BY clause
                select_node.group_by_clause = GroupByNode()
                
    # Now ensure GROUP BY clause contains all necessary columns
    if hasattr(select_node, 'group_by_clause'):
        group_by = select_node.group_by_clause
        # Clear existing GROUP BY expressions
        group_by.expressions = []
        # Store column reference strings already added to GROUP BY
        for expr, alias in select_node.select_expressions:
                        if not (hasattr(expr, 'function') and expr.function.func_type == 'aggregate'):
                            # Determine if it's a scalar function column
                            if (hasattr(expr, 'function') and expr.function.func_type == 'scalar') or type(expr).__name__=='ColumnReferenceNode':
                                    # Record number of GROUP BY expressions before addition
                                    before_count = len(group_by.expressions)
                                    # Directly add expression to GROUP BY
                                    group_by.add_expression(expr)
                                    # Record number of GROUP BY expressions after addition
                                    after_count = len(group_by.expressions)
                            # Determine window function
                            if hasattr(expr, 'function') and expr.function.func_type == 'window':
                                if hasattr(expr,'metadata'):
                                    if expr.metadata.get('partition_by'):
                                        partition_by=expr.metadata.get('partition_by')
                                        before_count = len(group_by.expressions)
                                        
                                        # Try to get from_node (assuming it's available in parent scope)
                                        if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            
                                            for part_expr in partition_by:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                         
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                                
                                                except Exception as e:
                                                    print(f"  Error converting partition_by expression: {e}")
                                        
                                        after_count = len(group_by.expressions)
                                    if expr.metadata.get('order_by'):
                                        order_by=expr.metadata.get('order_by')
                                        before_count = len(group_by.expressions)
                                        for order in order_by:
                                            expr_parts = order.rsplit(' ', 1)
                                        if len(expr_parts) == 2 and expr_parts[1].upper() in ['ASC', 'DESC']:
                                            main_expr = expr_parts[0]
                                            main_expr = [main_expr]
                                            sort_direction = expr_parts[1]
                                        else:
                                            main_expr = order_by
                                            sort_direction = None
                                        # Try to get from_node (assuming it's available in parent scope)
                                        if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            
                                            for part_expr in main_expr:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                        
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                        else:
                                                            # Special handling: If it's a subquery alias, create a virtual column reference
                                                        # Example: ORDER BY subq.max_email
                                                            virtual_col = Column(col_part, 'subquery', 'VARCHAR(255)', False, alias_part)
                                                            virtual_col_ref = ColumnReferenceNode(virtual_col, alias_part)
                                                            group_by.add_expression(virtual_col_ref)
                                                    else:
                                                        print(f"  Warning: Cannot parse order_by expression format: {part_expr}")
                                                except Exception as e:
                                                    print(f"  Error converting order_by expression: {e}")
                                        else:
                                            print("  Warning: from_node object not available, cannot convert order_by to ColumnReferenceNode")
                                        
                                        after_count = len(group_by.expressions)
                                        
                                        
                                        
                            # Check if select_node has order_by_clause attribute
                            if hasattr(select_node, 'order_by_clause') and select_node.order_by_clause:
                                # Iterate through expressions in order_by_clause
                                for expr, direction in select_node.order_by_clause.expressions:
                                    expr=[expr.to_sql()]
                                    # Can process these expressions as needed here
                                    if 'from_node' in locals() or 'from_node' in globals():
                                            available_from_node = locals().get('from_node') or globals().get('from_node')
                                            for part_expr in expr:
                                                try:
                                                    # Parse table alias and column name (format: table_alias.column_name)
                                                    if '.' in part_expr:
                                                        alias_part, col_part = part_expr.split('.', 1)
                                                        # Clean up possible quotes
                                                        alias_part = alias_part.strip('"\'')
                                                        col_part = col_part.strip('"\'')
                                                         
                                                        # Get table object
                                                        table_ref = available_from_node.get_table_for_alias(alias_part)
                                                        if table_ref and hasattr(table_ref, 'get_column'):
                                                            # Get column object
                                                            col = table_ref.get_column(col_part)
                                                            if col:
                                                                # Create ColumnReferenceNode object
                                                                col_ref = ColumnReferenceNode(col, alias_part)
                                                                # Add to GROUP BY
                                                                group_by.add_expression(col_ref)
                                                        else:
                                                            # Special handling: If it's a subquery alias, create a virtual column reference
                                                            # Example: ORDER BY subq.max_email
                                                            virtual_col = Column(col_part, 'subquery', 'VARCHAR(255)', False, alias_part)
                                                            virtual_col_ref = ColumnReferenceNode(virtual_col, alias_part)
                                                            group_by.add_expression(virtual_col_ref)
                                                    else:
                                                        print(f"  Warning: Cannot parse order_by expression format: {part_expr}")
                                                except Exception as e:
                                                    print(f"  Error converting order_by expression: {e}")
                                    else:
                                            print("  Warning: from_node object not available, cannot convert order_by to ColumnReferenceNode")
                    
    
    # After query generation, check and repair all function parameters
    fix_all_function_params(select_node, main_table, join_table if 'join_table' in locals() else None, main_alias, join_alias if 'join_alias' in locals() else None)
    # Execute function parameter fixing
    try:
        fix_all_function_params(select_node, main_table, join_table, main_alias, join_alias)
    except Exception as e:
        pass
    if use_cte:
        return f'{with_node.to_sql()} {select_node.to_sql()}'
    else:
        return select_node.to_sql()


def generate_index_sqls(tables, dialect):
    """Generate index SQL statements for tables, including enhanced composite index functionality, and add index information to Table objects
    
    Parameters:
    - tables: list of tables
    - dialect: database dialect
    
    Returns:
    - list of index SQL statements
    """
    import random
    import re
    index_sqls = []
    
    def is_text_blob_type(data_type):
        """Check if data type is TEXT or BLOB type"""
        data_type_lower = data_type.lower()
        # Basic TEXT/BLOB types
        if data_type_lower in ['text', 'longtext', 'mediumtext', 'blob', 'longblob', 'mediumblob']:
            return True
        # Check if contains 'text' or 'blob' keywords (handle variations)
        if 'text' in data_type_lower or 'blob' in data_type_lower:
            return True
        return False
    
    def get_varchar_length(data_type):
        """Extract length from VARCHAR type definition"""
        match = re.search(r'varchar\((\d+)\)', data_type.lower())
        if match:
            return int(match.group(1))
        return 255  # default length
    
    def get_column_with_key_length(col_name, data_type):
        """Add appropriate key length for TEXT/BLOB type and long VARCHAR type columns to avoid exceeding 255-byte limit"""
        data_type_lower = data_type.lower()
        
        # Handle TEXT/BLOB types
        if is_text_blob_type(data_type):
            # Add key length for TEXT type, use smaller value to avoid exceeding limit
            return f"{col_name}(50)"
        # Handle long VARCHAR types
        elif 'varchar' in data_type_lower:
            length = get_varchar_length(data_type)
            # For VARCHAR columns with length exceeding 100, add key length limitation
            if length > 100:
                # Use smaller key length, considering UTF8mb4 charset (each character up to 4 bytes)
                # Ensure key length does not exceed 255-byte limit
                key_length = min(63, length)  # 63 * 4 = 252 bytes, keep within limit
                return f"{col_name}({key_length})"
        return col_name
    
    # Generate index for each table
    for table in tables:
        # Initialize index list
        if table.indexes is None:
            table.indexes = []
            
        # First create unique index for primary key
        if table.primary_key:
            # Get primary key column information, check if it's TEXT/BLOB type
            pk_col = next((col for col in table.columns if col.name == table.primary_key), None)
            pk_col_with_length = table.primary_key
            if pk_col and is_text_blob_type(pk_col.data_type):
                # Primary key shouldn't be TEXT/BLOB type, but we handle this case for robustness
                pk_col_with_length = f"{table.primary_key}(100)"
                
            index_name = f"idx_{table.name}_pk"
            pk_index_sql = dialect.get_create_index_sql(
                table_name=table.name,
                index_name=index_name,
                columns=[pk_col_with_length],
                is_unique=True
            )
            index_sqls.append(pk_index_sql)
            # Add to table object's index information
            table.add_index(index_name, [table.primary_key], is_primary=True)
        
        # Now we divide all non-primary key columns into two categories:
        # 1. Direct index columns - non-TEXT/BLOB types
        # 2. Columns requiring key length - TEXT types (BLOB still excluded)
        direct_index_columns = []
        text_index_columns = []
        
        for col in table.columns:
            if col.name == table.primary_key:
                continue
                
            if is_text_blob_type(col.data_type):
                # Only allow TEXT types (excluding BLOB types) to create indexes
                if 'text' in col.data_type.lower() and 'blob' not in col.data_type.lower():
                    text_index_columns.append(col)
            else:
                direct_index_columns.append(col)
        
        # Merge all available index columns
        all_index_columns = direct_index_columns + text_index_columns
        
        # Only process if there are non-primary key columns suitable for indexing
        if all_index_columns:
            # Generate 1-2 single column indexes for each table
            num_single_indexes = random.randint(1, min(2, len(all_index_columns)))
            
            # Select columns to create single column indexes
            columns_to_index = random.sample(all_index_columns, num_single_indexes)
            
            # Create regular index for selected column
            for col in columns_to_index:
                index_name = f"idx_{table.name}_{col.name}"
                # Get column name with key length (if needed)
                col_with_length = get_column_with_key_length(col.name, col.data_type)
                index_sql = dialect.get_create_index_sql(
                    table_name=table.name,
                    index_name=index_name,
                    columns=[col_with_length],
                    is_unique=False
                )
                index_sqls.append(index_sql)
                # Add to table object's index information (store original column name without key length)
                table.add_index(index_name, [col.name])
            
            # Enhanced composite index generation logic
            if len(all_index_columns) >= 2:
                # Ensure at least one composite index is generated with 80% probability
                if random.random() < 0.8:
                    # Select 2-3 columns to create composite index
                    num_cols = random.randint(2, min(3, len(all_index_columns)))
                    composite_cols = random.sample(all_index_columns, num_cols)
                    
                    # Get list of column names with key lengths
                    col_names_with_length = [get_column_with_key_length(col.name, col.data_type) for col in composite_cols]
                    # Store original column names for index information
                    original_col_names = [col.name for col in composite_cols]
                    
                    index_name = f"idx_{table.name}_{'_'.join(original_col_names)}"
                    index_sql = dialect.get_create_index_sql(
                        table_name=table.name,
                        index_name=index_name,
                        columns=col_names_with_length,
                        is_unique=False
                    )
                    index_sqls.append(index_sql)
                    # Add to table object's index information (store original column names)
                    table.add_index(index_name, original_col_names)
                
                # If there are enough columns, possibly generate a second different composite index
                if len(all_index_columns) >= 4 and random.random() < 0.5:
                    # Ensure a different combination of columns is selected
                    remaining_cols = [col for col in all_index_columns if col not in columns_to_index[:2]]
                    if len(remaining_cols) >= 2:
                        num_cols = random.randint(2, min(3, len(remaining_cols)))
                        composite_cols = random.sample(remaining_cols, num_cols)
                        
                        # Get list of column names with key lengths
                        col_names_with_length = [get_column_with_key_length(col.name, col.data_type) for col in composite_cols]
                        # Store original column names for index information
                        original_col_names = [col.name for col in composite_cols]
                        
                        index_name = f"idx_{table.name}_{'_'.join(original_col_names)}"
                        index_sql = dialect.get_create_index_sql(
                            table_name=table.name,
                            index_name=index_name,
                            columns=col_names_with_length,
                            is_unique=False
                        )
                        index_sqls.append(index_sql)
                        # Add to table object's index information (store original column names)
                        table.add_index(index_name, original_col_names)
                
                # Generate a composite index that may include primary key (only if table has primary key)
                if table.primary_key and len(all_index_columns) >= 1 and random.random() < 0.3:
                    # Select 1-2 non-primary key columns to combine with primary key
                    num_cols = random.randint(1, min(2, len(all_index_columns)))
                    composite_cols = random.sample(all_index_columns, num_cols)
                    
                    # Get primary key column name with key length (if needed)
                    pk_col = next((col for col in table.columns if col.name == table.primary_key), None)
                    pk_with_length = table.primary_key
                    if pk_col and is_text_blob_type(pk_col.data_type):
                        pk_with_length = f"{table.primary_key}(100)"
                    
                    # Get list of non-primary key column names with key lengths
                    non_pk_cols_with_length = [get_column_with_key_length(col.name, col.data_type) for col in composite_cols]
                    # Combine columns (primary key usually comes first)
                    col_names_with_length = [pk_with_length] + non_pk_cols_with_length
                    # Store original column names for index information
                    original_col_names = [table.primary_key] + [col.name for col in composite_cols]
                    
                    index_name = f"idx_{table.name}_pk_{'_'.join([col.name for col in composite_cols])}"
                    index_sql = dialect.get_create_index_sql(
                        table_name=table.name,
                        index_name=index_name,
                        columns=col_names_with_length,
                        is_unique=True  # Indexes containing primary key are naturally unique
                    )
                    index_sqls.append(index_sql)
                    # Add to table object's index information (store original column names)
                    table.add_index(index_name, original_col_names)
    
    return index_sqls

def save_sql_to_file(sql: str, output_dir: str = "generated_sql", file_type: str = "all", mode: str = "w") -> str:
    """Save generated SQL to file
    
    Parameters:
    - sql: SQL statement
    - output_dir: output directory
    - file_type: file type ("schema", "query", or "all")
    - mode: write mode ("w" for overwrite, "a" for append)
    """
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Generate filename based on file type
    if file_type == "schema":
        filename = "schema.sql"
    elif file_type == "query":
        filename = "queries.sql"
    else:
        filename = "seedquery.sql"
    
    filepath = os.path.join(output_dir, filename)

    # Get current database dialect
    from data_structures.db_dialect import get_current_dialect
    dialect = get_current_dialect()

    # Write to file
    with open(filepath, mode, encoding="utf-8") as f:
        if mode == "w":  # Only write database settings in overwrite mode
            if file_type == "schema":
                # Use dialect's method to generate database operation statements
                drop_db_sql = dialect.get_drop_database_sql("test")
                create_db_sql = dialect.get_create_database_sql("test")
                f.write(drop_db_sql)
                if drop_db_sql and not drop_db_sql.endswith("\n"):
                    f.write("\n")
                f.write(create_db_sql)
                if create_db_sql and not create_db_sql.endswith("\n"):
                    f.write("\n")
            
            # Use dialect's method to generate USE database statement
            use_db_sql = dialect.get_use_database_sql("test")
            if use_db_sql:
                f.write(use_db_sql)
                if not use_db_sql.endswith("\n"):
                    f.write("\n")
            else:
                # If no USE statement (like PostgreSQL), add comment explaining connection method
                f.write("-- PostgreSQL doesn't have USE statement, database connection is specified through connection parameters\n")
        f.write(sql)

    return filepath


from typing import Optional, Dict
# ------------------------------
# Main Function
# ------------------------------

def Generate(subquery_depth: int = 3, total_insert_statements: int = 100, num_queries: int = 15, query_type: str = 'default', use_database_tables: bool = False, db_config: Optional[Dict] = None):
    """Main function: Generate and save SQL statements (table creation, insertion, and query)

    Parameters:
    - subquery_depth: maximum depth of subqueries
    - total_insert_statements: total number of insert statements to generate
    - num_queries: number of query statements to generate
    - query_type: query type, 'default' uses generate_random_sql(), 'aggregate' uses generate_random_sql_with_aggregate()
    - use_database_tables: whether to use table structures from database, default is False
    - db_config: database connection configuration, must be provided when use_database_tables is True
    """
    
    # Select table structure source
    if use_database_tables:
        if db_config is None:
            print("Error: db_config parameter must be provided when using database tables")
            return
        
        # Import DatabaseMetadataFetcher
        from database_metadata_fetcher import DatabaseMetadataFetcher
        
        # Create database metadata fetcher
        fetcher = DatabaseMetadataFetcher(
            host=db_config.get('host', '127.0.0.1'),
            port=db_config.get('port', 4000),
            database=db_config.get('database', 'test'),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', '123456'),
            dialect=db_config.get('dialect', 'MYSQL')
        )
        
        # Connect to database and get table information
        if fetcher.connect():
            tables = fetcher.get_all_tables_info()
            fetcher.disconnect()
            print(f"Retrieved structure information for {len(tables)} tables from database")
            
            # Save table information as Markdown file
            if tables:
                # Ensure generated_sql directory exists
                os.makedirs("generated_sql", exist_ok=True)
                md_file_path = os.path.join("generated_sql", "database_tables_info.md")
                
                try:
                    with open(md_file_path, 'w', encoding='utf-8') as md_file:
                        md_file.write("# Database Table Structure Information\n")
                        md_file.write(f"\n## Database Connection Information\n")
                        md_file.write(f"- **Host**: {db_config.get('host', '127.0.0.1')}\n")
                        md_file.write(f"- **Port**: {db_config.get('port', 4000)}\n")
                        md_file.write(f"- **Database**: {db_config.get('database', 'test')}\n")
                        md_file.write(f"- **Dialect**: {db_config.get('dialect', 'MYSQL')}\n")
                        md_file.write(f"- **Table Count**: {len(tables)}\n")
                        
                        md_file.write(f"\n## Table Details\n\n")
                        
                        for table in tables:
                            md_file.write(f"### {table.name}\n")
                            md_file.write(f"- **Primary Key**: {table.primary_key if table.primary_key else 'None'}\n")
                            
                            # Table column information
                            md_file.write("\n| Column Name | Data Type | Category | Nullable |\n")
                            md_file.write("|------|---------|------|---------|\n")
                            for col in table.columns:
                                md_file.write(f"| {col.name} | {col.data_type} | {col.category} | {'Yes' if col.is_nullable else 'No'} |\n")
                            
                            # Foreign key information
                            if table.foreign_keys:
                                md_file.write("\n#### Foreign Key Relationships\n")
                                for fk in table.foreign_keys:
                                    md_file.write(f"- `{fk['column']}` -> `{fk['ref_table']}.{fk['ref_column']}`\n")
                            
                            md_file.write("\n")
                    
                    print(f"Database table structure information saved to: {md_file_path}")
                except Exception as e:
                    print(f"Failed to save database table structure information: {e}")
        else:
            print("Database connection failed, using sample table structures")
            tables = create_sample_tables()
    else:
        # Use sample table structures
        tables = create_sample_tables()
    
    functions = create_sample_functions()
    global SUBQUERY_DEPTH
    SUBQUERY_DEPTH = subquery_depth
    
    # Set global table structure information
    global TABLES
    TABLES = tables
    set_tables(tables)
    
    # Record whether database tables are being used
    is_using_database_tables = use_database_tables and len(tables) > 0 and tables[0].name != "users"
    
    
    # Only generate create table and insert statements when not using database tables
    if not is_using_database_tables:
        # Generate create table statements
        create_sqls = []
        for table in tables:
            create_sql = generate_create_table_sql(table)
            create_sqls.append(create_sql)

        # Generate insert statements
        insert_sqls = []
        # Store primary key values for each table for foreign key references
        primary_keys_dict = {}
        # Calculate insert rows per table (total insert statements evenly distributed to each table)
        num_tables = len(tables)
        insert_rows_per_table = total_insert_statements // num_tables
        remainder = total_insert_statements % num_tables

        # Store insert rows per table
        table_insert_rows = {}

        # First generate primary key values for all tables
        for i, table in enumerate(tables):
            # Generate primary key values for table
            primary_key_values = set()
            # Distribute insert rows, first 'remainder' tables get 1 extra row
            num_rows = insert_rows_per_table + (1 if i < remainder else 0)
            table_insert_rows[table.name] = num_rows
            for _ in range(num_rows):
                while True:
                    val = random.randint(1, 10000)
                    if val not in primary_key_values:
                        primary_key_values.add(val)
                        break
            primary_keys_dict[table.name] = list(primary_key_values)

        # Generate insert statements in correct order (insert referenced tables first)
        # Use simple topological sort to determine table insertion order
        visited = set()
        def topological_sort(table_name):
            if table_name in visited:
                return
            visited.add(table_name)
            table = next(t for t in tables if t.name == table_name)
            # Process all referenced tables first
            for fk in table.foreign_keys:
                ref_table = fk["ref_table"]
                if ref_table not in visited:
                    topological_sort(ref_table)
            # Generate insert statement for current table
            num_rows = table_insert_rows[table.name]
            insert_sql = generate_insert_sql(table, num_rows=num_rows, existing_primary_keys=primary_keys_dict, primary_key_values=primary_keys_dict[table.name])
            insert_sqls.append(insert_sql)

        # Perform topological sort on all tables and generate insert statements
        for table in tables:
            if table.name not in visited:
                topological_sort(table.name)

        # Generate index statements
        from data_structures.db_dialect import get_current_dialect
        dialect = get_current_dialect()
        index_sqls = generate_index_sqls(tables, dialect)
        
        # Combine create table, insert, and index statements
        schema_sql = "\n\n".join(create_sqls + insert_sqls + index_sqls)
        schema_filepath = save_sql_to_file(schema_sql, file_type="schema")
    else:
        # When using database tables, don't generate create table and insert statements
        print("Using database table structures, skipping creation of table and insert statements")
        schema_filepath = "[Using database tables, schema file not generated]"
    
    # Generate and write query statements in batches to reduce memory usage
    batch_size = 1000  # Number of queries processed per batch
    query_filepath = save_sql_to_file("", file_type="query")  # Create empty file and write USE test;
    
    # Save index SQL to query file
    if not is_using_database_tables:
        # Generate index statements (if not already generated)
        if 'index_sqls' not in locals():
            from data_structures.db_dialect import get_current_dialect
            dialect = get_current_dialect()
            index_sqls = generate_index_sqls(tables, dialect)
        # Write index SQL to query file
        if index_sqls:
            index_sql_content = "\n\n".join(index_sqls)
            save_sql_to_file(index_sql_content, file_type="query", mode="a")
            # Add separator
            save_sql_to_file("\n\n", file_type="query", mode="a")
            
            # Also save index SQL to indexes.sql file
            # Create indexes.sql file path
            indexes_file_path = os.path.join("generated_sql", "indexes.sql")
            # Write USE test statement and index SQL
            with open(indexes_file_path, "a", encoding="utf-8") as f:
                # Get current database dialect
                from data_structures.db_dialect import get_current_dialect
                dialect = get_current_dialect()
                # Write USE statement
                use_db_sql = dialect.get_use_database_sql("test")
                if use_db_sql:
                    f.write(use_db_sql)
                    if not use_db_sql.endswith("\n"):
                        f.write("\n")
                # Write index SQL
                f.write(index_sql_content)
    
    # Error log file path
    error_log_path = os.path.join("generated_sql", "query_generation_errors.log")
    
    # Open error log file for writing
    with open(error_log_path, "w", encoding="utf-8") as error_log:
        error_log.write(f"# SQL Query Generation Error Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        error_log.write(f"# Target Query Count: {num_queries}\n")
        error_log.write("\n")
    
    for i in range(0, num_queries, batch_size):
        # Calculate the number of queries for the current batch
        current_batch_size = min(batch_size, num_queries - i)
        
        # Generate queries for the current batch
        batch_queries = []
        # Statistics
        success_count = 0
        fail_count = 0
        error_types = {}
        
        
        for j in range(current_batch_size):
            retry_count = 0
            max_retries = 10  # Increase maximum retry count to improve success rate
            success = False
            error_info = None
            error_type = None
            
            # Initialize error-related variables to avoid undefined errors
            error_type = "UnknownError"
            error_message = ""
            e = Exception("Reached maximum retry count without capturing specific exception")
            
            while retry_count < max_retries:
                try:
                    sql = generate_random_sql(tables, functions)
                    batch_queries.append(sql)
                    success = True
                    success_count += 1
                    
                    # Print log every 100 queries generated
                    if (i + j + 1) % 100 == 0:
                        pass
                    break  # Generation successful, exit retry loop
                except Exception as e:
                    retry_count += 1
                    # Record error information, but don't update error type statistics immediately
                    error_type = type(e).__name__
                    error_message = str(e)[:100]  # Limit error message length
                    error_info = f"{error_type}: {error_message}"
                    
                    # Print log every 3 retries to avoid excessive logging
                    # Error retry printing has been removed
            
            if not success:
                fail_count += 1
                
                # Only update error type statistics when query ultimately fails (count each failed query only once)
                error_types[error_type] = error_types.get(error_type, 0) + 1
                
                # Write error log with detailed error information
                with open(error_log_path, "a", encoding="utf-8") as error_log:
                    error_log.write(f"[{i+j+1}/{num_queries}] Generation Failed:\n")
                    error_log.write(f"  Error Type: {error_type}\n")
                    error_log.write(f"  Error Message: {error_message}\n")
                    error_log.write(f"  Retry Count: {max_retries}\n")
                    error_log.write("\n")
            
            # Print progress and statistics every 100 queries generated
            if (i + j + 1) % 100 == 0:
                pass
            
            # Progress indication
            if (i + j + 1) % 5000 == 0:
                print(f"Generated {i + j + 1}/{num_queries} query statements")
        
        # Write current batch of queries to file
        batch_sql = "\n\n".join(batch_queries)
        save_sql_to_file(batch_sql, file_type="query", mode="a")
        
        # Record batch completion information to error log
        with open(error_log_path, "a", encoding="utf-8") as error_log:
            error_log.write(f"=== Batch {i//batch_size + 1} Completed ===\n")
            error_log.write(f"Batch Query Total: {current_batch_size}\n")
            error_log.write(f"Successfully Generated: {success_count}\n")
            error_log.write(f"Failed to Generate: {fail_count}\n")
            error_log.write(f"Error Type Statistics: {error_types}\n")
            error_log.write("\n")
        
        # Print batch completion information
        print(f"=== Batch {i//batch_size + 1} Completed ===")
        print(f"Total batch queries: {current_batch_size}")
        print(f"Successfully generated: {success_count}")
        print(f"Generation failed: {fail_count}")
        print(f"Error type statistics: {error_types}")
        
        # Add separator if not the last batch
        if i + current_batch_size < num_queries:
            save_sql_to_file("\n\n", file_type="query", mode="a")        
        # Clear batch list to free memory
        batch_queries = []
    # Record final statistics to error log
    with open(error_log_path, "a", encoding="utf-8") as error_log:
        total_success = len([line for line in open(query_filepath, 'r', encoding='utf-8') if line.strip()])
        total_fail = num_queries - total_success
        error_log.write("=== Final Statistics ===\n")
        error_log.write(f"Target Query Count: {num_queries}\n")
        error_log.write(f"Successfully Generated: {total_success}\n")
        error_log.write(f"Failed to Generate: {total_fail}\n")
        error_log.write(f"Generation Rate: {(total_success/num_queries)*100:.2f}%\n")
        error_log.write(f"\nGeneration Log Creation Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Print final result information
    if not is_using_database_tables:
        print(f"Schema SQL saved to: {schema_filepath}")
    else:
        print("Using database table structures, schema file not generated")
    print(f"Query SQL saved to: {query_filepath}")
    print(f"Error log saved to: {error_log_path}")
    print(f"Target query count: {num_queries}")
    print(f"Actual generated count: {len(batch_queries) + (i if i > 0 else 0)}")
    print(f"Generation rate: {(len(batch_queries) + (i if i > 0 else 0))/num_queries*100:.2f}%")


