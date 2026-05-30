# FunctionCallNode class definition - Function call node
from typing import Set, Optional
import random
from .ast_node import ASTNode
from .column_reference_node import ColumnReferenceNode
from .literal_node import LiteralNode
from .arithmetic_node import ArithmeticNode
from .case_node import CaseNode
from data_structures.node_type import NodeType
from data_structures.function import Function
from data_structures.db_dialect import get_dialect_config

class FunctionCallNode(ASTNode):
    """Function call node - Enhanced version with parameter type validation"""

    def __init__(self, function: Function):
        super().__init__(NodeType.FUNCTION_CALL)
        self.function = function
        self.category = None
        self.metadata = {
            'function_name': function.name,
            'return_type': function.return_type,
            'is_aggregate': function.func_type == 'aggregate',  # Mark whether it's an aggregate function
            'func_type': function.func_type  # Save function type for checking
        }
        
    @property
    def data_type(self):
        """Provide accessor for data_type property to avoid AttributeError"""
        return self.metadata.get('return_type', 'unknown')

    def collect_table_aliases(self) -> Set[str]:
        """Collect all table aliases referenced in function parameters"""
        aliases = set()
        # Recursively collect table alias references from all parameter nodes
        for child in self.children:
            aliases.update(child.collect_table_aliases())
        return aliases

    def add_child(self, child: ASTNode) -> bool:
        """Add function parameter and validate type

        Returns:
            bool: Whether the parameter was added successfully
        """
        # Check if maximum parameter count is reached
        if self.function.max_params is not None and len(self.children) >= self.function.max_params:
            return False

        # Parameter type validation
        param_index = len(self.children)
        if param_index < len(self.function.param_types):
            expected_type = self.function.param_types[param_index]
            
            # Special handling for SUBSTRING and ROUND functions
            if self.function.name == 'SUBSTRING':
                if param_index == 1:  # Second parameter: start position
                    # Ensure it's a positive integer (PostgreSQL requires integer type)
                    if isinstance(child, LiteralNode):
                        if isinstance(child.value, (int, float)):
                            if child.value <= 0:
                                child = LiteralNode(random.randint(1, 10), 'INT')
                            else:
                                # Ensure integer type is used instead of numeric/float
                                child = LiteralNode(int(child.value), 'INT')
                    elif isinstance(child, ColumnReferenceNode):
                        # Check if column is integer type
                        col_category = child.column.category
                        if col_category != 'int':
                            # If column is not integer type, use integer literal
                            child = LiteralNode(random.randint(1, 10), 'INT')
                    elif not self._is_valid_param_type(child, expected_type):
                        child = LiteralNode(random.randint(1, 10), 'INT')
                elif param_index == 2:  # Third parameter: length
                    # Ensure it's a non-negative integer (PostgreSQL requires integer type)
                    if isinstance(child, LiteralNode):
                        if isinstance(child.value, (int, float)):
                            if child.value < 0:
                                child = LiteralNode(random.randint(1, 20), 'INT')
                            else:
                                # Ensure integer type is used instead of numeric/float
                                child = LiteralNode(int(child.value), 'INT')
                    elif isinstance(child, ColumnReferenceNode):
                        # Check if column is integer type
                        col_category = child.column.category
                        if col_category != 'int':
                            # If column is not integer type, use integer literal
                            child = LiteralNode(random.randint(1, 20), 'INT')
                    elif not self._is_valid_param_type(child, expected_type):
                        child = LiteralNode(random.randint(1, 20), 'INT')
                elif not self._is_valid_param_type(child, expected_type):
                    # First parameter or other parameter types do not match
                    if expected_type == 'string':
                        child = LiteralNode(f'str_{random.randint(1, 100)}', 'STRING')
                    else:
                        return False
            elif self.function.name == 'DATE_FORMAT':
                # Strictly ensure the second parameter of DATE_FORMAT function is a valid format string literal
                    # This complies with MySQL standard usage requirements
                if param_index == 1:  # Second parameter: format string
                    # Force use of standard MySQL datetime format string
                    # Replace with predefined format string literal regardless of original parameter type
                    format_options = ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%i:%s', '%H:%i:%s']
                    child = LiteralNode(random.choice(format_options), 'STRING')
            elif self.function.name == 'ROUND':
                if param_index == 0:  # First parameter: value to be rounded
                    # Check if it's timestamp type, convert to numeric type if needed
                    from data_structures.db_dialect import get_dialect_config
                    dialect = get_dialect_config()
                    
                    if dialect.name == 'POSTGRESQL':
                        # Check if first parameter is timestamp type
                        if isinstance(child, ColumnReferenceNode):
                            col_category = child.column.category
                            if col_category in ['datetime', 'timestamp', 'date']:
                                # Create EXTRACT function call to convert timestamp to Unix timestamp
                                from data_structures.function import Function
                                extract_func = Function('EXTRACT', 'numeric', ['string', 'date'])
                                extract_node = FunctionCallNode(extract_func)
                                extract_node.add_child(LiteralNode('EPOCH FROM', 'STRING'))
                                extract_node.add_child(child)
                                child = extract_node
                        elif isinstance(child, FunctionCallNode):
                            return_type = child.metadata.get('return_type')
                            if return_type in ['datetime', 'timestamp', 'date']:
                                # Create EXTRACT function call to convert timestamp to Unix timestamp
                                from data_structures.function import Function
                                extract_func = Function('EXTRACT', 'numeric', ['string', 'date'])
                                extract_node = FunctionCallNode(extract_func)
                                extract_node.add_child(LiteralNode('EPOCH FROM', 'VARCHAR(20)'))
                                extract_node.add_child(child)
                                child = extract_node
                        elif isinstance(child, LiteralNode):
                            # Check if LiteralNode is date/time type
                            if hasattr(child, 'data_type'):
                                data_type = child.data_type.lower()
                                if data_type in ['date', 'datetime', 'timestamp']:
                                    # Create EXTRACT function call to convert timestamp to Unix timestamp
                                    from data_structures.function import Function
                                    extract_func = Function('EXTRACT', 'numeric', ['string', 'date'])
                                    extract_node = FunctionCallNode(extract_func)
                                    extract_node.add_child(LiteralNode('EPOCH FROM', 'VARCHAR(20)'))
                                    extract_node.add_child(child)
                                    child = extract_node
                                # Create EXTRACT function call to convert timestamp to Unix timestamp
                                from data_structures.function import Function
                                extract_func = Function('EXTRACT', 'numeric', ['string', 'date'])
                                extract_node = FunctionCallNode(extract_func)
                                extract_node.add_child(LiteralNode('EPOCH FROM', 'VARCHAR(20)'))
                                extract_node.add_child(child)
                                child = extract_node
                elif param_index == 1:  # Second parameter: decimal places
                    # PostgreSQL requires the second parameter of ROUND function to be integer type
                    if isinstance(child, LiteralNode):
                        if isinstance(child.value, (int, float)):
                            # Convert to integer and ensure INT type
                            child = LiteralNode(int(child.value), 'INT')
                    elif isinstance(child, ColumnReferenceNode):
                        # Check if column is integer type
                        col_category = child.column.category
                        if col_category != 'int':
                            # If column is not integer type, use integer literal
                            child = LiteralNode(random.randint(0, 5), 'INT')
                    elif not self._is_valid_param_type(child, expected_type):
                        child = LiteralNode(random.randint(0, 5), 'INT')
            elif not self._is_valid_param_type(child, expected_type):
                # Type validation for other functions

                if self.function.func_type == 'aggregate':
                    # Create matching type literal
                    if expected_type == 'numeric':
                        child = LiteralNode(random.randint(1, 100), 'INT')
                    elif expected_type == 'string':
                        child = LiteralNode(f'sample_{random.randint(1, 100)}', 'STRING')
                    elif expected_type == 'datetime':
                        child = LiteralNode('2023-01-01', 'DATE')
                    else:
                        # Default to numeric type
                        child = LiteralNode(random.randint(1, 100), 'INT')
                else:
                    return False  # Strict type checking for non-aggregate functions

        super().add_child(child)
        return True

    def _is_valid_param_type(self, child: ASTNode, expected_type: str) -> bool:
        """Validate if parameter type matches

        Args:
            child: Parameter node
            expected_type: Expected parameter type

        Returns:
            bool: Whether the parameter type is valid
        """
        if expected_type == 'any':
            # For aggregate functions, special handling for geometry type columns
            if self.metadata.get('is_aggregate') and isinstance(child, ColumnReferenceNode):
                # Check if column data type is a geometry type
                if child.column.data_type in ['GEOMETRY', 'POINT', 'LINESTRING', 'POLYGON']:
                    return False  # Aggregate functions do not support geometry types
            return True  # Non-aggregate functions or non-geometry types accept any type

        if isinstance(child, ColumnReferenceNode):
            # Column reference node, check column type
            col_category = child.column.category
            return col_category == expected_type or (expected_type == 'numeric' and col_category in ['int', 'float', 'decimal'])
        elif isinstance(child, LiteralNode):
            # Literal node, check data type
            if hasattr(child, 'data_type'):
                data_type = child.data_type.lower()
                if expected_type == 'numeric':
                    return data_type in ['int', 'float', 'decimal', 'numeric']
                elif expected_type == 'string':
                    return data_type in ['varchar', 'string', 'char']
                elif expected_type == 'datetime':
                    return data_type in ['date', 'datetime', 'timestamp']
                return data_type == expected_type
            # If no data_type attribute, return False
                return data_type in ['int', 'float', 'decimal', 'numeric']
            elif expected_type == 'string':
                return data_type in ['varchar', 'string', 'char']
            elif expected_type == 'datetime':
                return data_type in ['date', 'datetime', 'timestamp']
            return data_type == expected_type
        elif isinstance(child, FunctionCallNode):
            # Function call node, check return type
            return child.metadata.get('return_type') == expected_type
        elif isinstance(child, ArithmeticNode):
            # Arithmetic expression node, result is usually numeric
            return expected_type == 'numeric'
        elif isinstance(child, CaseNode):
            # CASE expression, check result type
            result_type = child.metadata.get('result_type', 'unknown')
            return result_type == expected_type or expected_type == 'any'

        return False

    def to_sql(self) -> str:
        # Get current dialect configuration
        dialect = get_dialect_config()
        
        # Get dialect-specific function name
        function_name = dialect.get_function_name(self.function.name)
        
        # Special handling for some functions
        if self.function.name == 'DATE_FORMAT' and dialect.get_function_name('DATE_FORMAT') == 'TO_CHAR':
              # In PostgreSQL, DATE_FORMAT converts to TO_CHAR with different parameter order
              if len(self.children) >= 2:
                  # TO_CHAR(date, format)
                  date_arg = self.children[0].to_sql()
                  # Convert MySQL format to PostgreSQL format
                  mysql_format = self.children[1].to_sql().strip("'")
                  pg_format = mysql_format.replace('%Y', 'YYYY').replace('%m', 'MM').replace('%d', 'DD')
                  pg_format = pg_format.replace('%H', 'HH24').replace('%i', 'MI').replace('%s', 'SS')
                  return f"TO_CHAR({date_arg}, '{pg_format}')"
        
        # Special handling for COUNT_DISTINCT function
        if self.function.name == 'COUNT_DISTINCT':
            # Ensure there are parameters
            if self.children:
                arg_sql = self.children[0].to_sql()
                return f"COUNT(DISTINCT {arg_sql})"
        
        # Special handling for SUM_DISTINCT function
        if self.function.name == 'SUM_DISTINCT':
            # Ensure there are parameters
            if self.children:
                arg_sql = self.children[0].to_sql()
                return f"SUM(DISTINCT {arg_sql})"
        
        # Function call arguments SQL
        args = [child.to_sql() for child in self.children]
        
        # Special handling for GROUP_CONCAT function, add ORDER BY clause
        if self.function.name == 'GROUP_CONCAT' and args:
            # Use the first parameter as the ordering basis for ORDER BY clause
            order_by_arg = args[0]
            return f"GROUP_CONCAT({', '.join(args)} ORDER BY {order_by_arg})"

        # Window function special handling
        if self.function.func_type == 'window':
            # Check if it's Percona dialect, Percona 5.7 does not support window functions
            from data_structures.db_dialect import get_current_dialect
            current_dialect = get_current_dialect()
            is_percona = current_dialect and 'PerconaDialect' == current_dialect.__class__.__name__
            
            # In Percona dialect, convert window functions to regular function calls
            if is_percona:
                # For parameterless window functions like ROW_NUMBER, RANK, return a default value
                if self.function.name in ['ROW_NUMBER', 'RANK', 'DENSE_RANK', 'NTILE']:
                    return '1'  # Return a constant value as replacement
                # For other window functions, attempt to call as regular functions
                else:
                    if args:
                        return f"{function_name}({', '.join(args)})"
                    else:
                        return f"{function_name}()"
            
            # Normal window function handling for non-Percona dialects
            partition_by = self.metadata.get('partition_by', [])
            order_by = self.metadata.get('order_by', [])

            window_parts = []
            if partition_by:
                window_parts.append(f"PARTITION BY {', '.join(partition_by)}")
            
            # For window functions that require ORDER BY, if no ORDER BY is present, add a default ORDER BY clause
            # Including ROW_NUMBER, RANK, DENSE_RANK, LEAD, LAG functions
            if not order_by and self.function.name in ['ROW_NUMBER', 'RANK', 'DENSE_RANK', 'NTILE', 'LEAD', 'LAG']:
                # Use constant expression as ordering basis to avoid using positional references not supported by MySQL
                order_by = ['1=1']  # Use logical expression instead of positional reference
                window_parts.append(f"ORDER BY {', '.join(order_by)}")
            elif order_by:
                window_parts.append(f"ORDER BY {', '.join(order_by)}")

            window_clause = f"OVER ({' '.join(window_parts)})"
            if args:
                return f"{function_name}({', '.join(args)}) {window_clause}"
            else:
                return f"{function_name}() {window_clause}"  # Window function with no parameters
        else:
            # Regular function call
            return f"{function_name}({', '.join(args)})"

    def collect_column_aliases(self) -> Set[str]:
        """Collect column aliases referenced in function parameters"""
        aliases = set()
        for child in self.children:
            aliases.update(child.collect_column_aliases())
        return aliases