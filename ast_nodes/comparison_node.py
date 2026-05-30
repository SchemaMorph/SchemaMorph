# ComparisonNode class definition - Comparison expression node
from .ast_node import ASTNode
from .column_reference_node import ColumnReferenceNode
from .literal_node import LiteralNode
from .function_call_node import FunctionCallNode
from .arithmetic_node import ArithmeticNode
from data_structures.node_type import NodeType
from typing import Set, Tuple, List

class ComparisonNode(ASTNode):
    """Comparison expression node"""

    def __init__(self, operator: str):
        super().__init__(NodeType.COMPARISON)
        # Extended operator list, containing more SQL standard operators
        self.supported_operators = {
            '=', '<>', '!=', '<', '>', '<=', '>=',
            'LIKE', 'NOT LIKE', 'RLIKE', 'REGEXP', 'NOT REGEXP',
            'IS NULL', 'IS NOT NULL', 'IN', 'NOT IN',
            'BETWEEN', 'NOT BETWEEN', 'EXISTS', 'NOT EXISTS'
        }
        
        # Validate if the operator is supported
        if operator not in self.supported_operators:
            raise ValueError(f"Unsupported operator: {operator}")
        
        self.operator = operator
        self.metadata = {
            'operator': operator,
            'is_aggregate': False  # Comparison expressions are typically not aggregates, but will be updated based on child nodes
        }

    def to_sql(self) -> str:
        if len(self.children) == 0:
            return ""

        left = self.children[0].to_sql() if self.children else ""

        # Single operand operators
        if self.operator in ['IS NULL', 'IS NOT NULL']:
            return f"{left} {self.operator}"
        
        # EXISTS/NOT EXISTS operators
        if self.operator in ['EXISTS', 'NOT EXISTS']:
            if len(self.children) >= 1:
                subquery_sql = self.children[0].to_sql()
                # Ensure subquery is enclosed in parentheses
                if not (subquery_sql.startswith('(') and subquery_sql.endswith(')')):
                    subquery_sql = f"({subquery_sql})"
                return f"{self.operator} {subquery_sql}"
            return ""

        # Two operand operators
        if len(self.children) < 2:
            return f"{left} {self.operator}"
            
        right = self.children[1].to_sql()

        # IN/NOT IN operators
        if self.operator in ['IN', 'NOT IN'] and right:
            # Check if the right side is already in subquery format
            if not (right.startswith('(') and right.endswith(')')):
                # If not a subquery, assume it's a list
                return f"{left} {self.operator} ({right})"
            else:
                # If already in subquery format, use directly
                return f"{left} {self.operator} {right}"

        # BETWEEN/NOT BETWEEN operators
        if self.operator in ['BETWEEN', 'NOT BETWEEN'] and len(self.children) >= 3:
            right1 = self.children[1].to_sql()
            right2 = self.children[2].to_sql()
            return f"{left} {self.operator} {right1} AND {right2}"

        # Other two-operand operators
        # Check if REGEXP operator needs dialect adaptation
        from data_structures.db_dialect import get_dialect_config
        dialect = get_dialect_config()
        operator = self.operator
        
        # PostgreSQL adaptation: Convert REGEXP, NOT REGEXP, RLIKE and NOT RLIKE to ~ and !~
        if dialect.name == 'POSTGRESQL':
            if operator == 'REGEXP' or operator == 'RLIKE':
                operator = '~'
            elif operator == 'NOT REGEXP' or operator == 'NOT RLIKE':
                operator = '!~'
        # PolarDB adaptation: PolarDB does not support RLIKE and NOT RLIKE operators, use LIKE instead
        elif dialect.name == 'POLARDB':
            if operator == 'RLIKE':
                operator = 'LIKE'
            elif operator == 'NOT RLIKE':
                operator = 'NOT LIKE'
        
        return f"({left} {operator} {right})"

    def collect_table_aliases(self) -> Set[str]:
        """Collect table aliases referenced in the comparison expression"""
        aliases = set()
        for child in self.children:
            aliases.update(child.collect_table_aliases())
        return aliases

    def collect_column_aliases(self) -> Set[str]:
        """Collect column aliases referenced in the comparison expression"""
        aliases = set()
        for child in self.children:
            aliases.update(child.collect_column_aliases())
        return aliases

    def _is_type_compatible(self, left_type, right_type):
        """Check if two data types are compatible"""
        # Ideally, we should use the category property of the Column object to determine type compatibility
        # However, since this method is designed to directly receive type strings rather than Column objects
        # We temporarily retain the judgment logic based on data type string keywords
        
        # Define type compatibility rules
        numeric_types = {'INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}
        string_types = {'VARCHAR', 'CHAR', 'TEXT', 'LONGTEXT', 'MEDIUMTEXT', 'TINYTEXT'}
        datetime_types = {'DATE', 'DATETIME', 'TIMESTAMP', 'TIME'}
        
        # Extract basic type (remove parentheses and length information)
        base_type1 = left_type.split('(')[0].upper() if left_type else 'UNKNOWN'
        base_type2 = right_type.split('(')[0].upper() if right_type else 'UNKNOWN'
        
        # Strict type matching: must belong to the same type group
        if (base_type1 in numeric_types and base_type2 in numeric_types) or \
           (base_type1 in string_types and base_type2 in string_types) or \
           (base_type1 in datetime_types and base_type2 in datetime_types) or \
           base_type1 == base_type2:
            return True
        
        return False
        
    def _get_node_type(self, node):
        """Get the data type of a node"""
        if isinstance(node, ColumnReferenceNode):
            return node.column.data_type
        elif isinstance(node, LiteralNode):
            return node.data_type
        elif isinstance(node, FunctionCallNode):
            return node.metadata.get('return_type', '')
        elif hasattr(node, 'metadata') and 'data_type' in node.metadata:
            return node.metadata['data_type']
        return ''
        
    def validate_columns(self, from_node: 'FromNode') -> Tuple[bool, List[str]]:
        """Validate column references in the comparison expression, including type compatibility check"""
        errors = []
        
        # First, validate all child node column references
        for child in self.children:
            if hasattr(child, 'validate_columns'):
                valid, child_errors = child.validate_columns(from_node)
                if not valid:
                    errors.extend(child_errors)
            elif isinstance(child, ColumnReferenceNode):
                if not child.is_valid(from_node):
                    errors.append(f"Invalid column reference: {child.to_sql()}")
        
        # For binary comparison operators, check type compatibility of left and right sides
        if self.operator in ['=', '<>', '!=', '<', '>', '<=', '>=', 'LIKE', 'NOT LIKE', 'RLIKE', 'REGEXP', 'NOT REGEXP']:
            if len(self.children) >= 2:
                left_type = self._get_node_type(self.children[0])
                right_type = self._get_node_type(self.children[1])
                
                if left_type and right_type and not self._is_type_compatible(left_type, right_type):
                    errors.append(f"Type mismatch: {self.children[0].to_sql()} ({left_type}) and {self.children[1].to_sql()} ({right_type}) in comparison operation {self.operator}")
        
        return (len(errors) == 0, errors)

    def repair_columns(self, from_node: 'FromNode') -> None:
        """Repair invalid column references and type incompatibility issues in the comparison expression"""
        for i, child in enumerate(self.children):
            if hasattr(child, 'repair_columns'):
                child.repair_columns(from_node)
            elif isinstance(child, ColumnReferenceNode) and not child.is_valid(from_node):
                replacement = child.find_replacement(from_node)
                if replacement:
                    self.children[i] = replacement
        
        # Special handling for BETWEEN operator type compatibility issues
        if self.operator in ['BETWEEN', 'NOT BETWEEN'] and len(self.children) >= 3:
            # Get the type of the left column
            left_type = self._get_node_type(self.children[0])
            left_node = self.children[0]
            
            # Check if the left side has a valid type
            if left_type:
                base_left_type = left_type.split('(')[0].upper()
                
                # Handle both boundary values (BETWEEN value1 AND value2)
                for i in [1, 2]:
                    right_node = self.children[i]
                    right_type = self._get_node_type(right_node)
                    
                    # Check if the right node has a valid type and is incompatible with the left type
                    if right_type and not self._is_type_compatible(left_type, right_type):
                        from data_structures.db_dialect import get_dialect_config
                        dialect = get_dialect_config()
                        
                        # Perform type conversion based on database dialect
                        if dialect.name == 'POSTGRESQL':
                            from data_structures.function import Function
                            
                            # Handle datetime type conversion
                            datetime_types = {'DATE', 'DATETIME', 'TIMESTAMP'}
                            numeric_types = {'INT', 'INTEGER', 'BIGINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}
                            string_types = {'VARCHAR', 'TEXT', 'CHAR'}
                            
                            base_right_type = right_type.split('(')[0].upper()
                            
                            # Left side is datetime type, right side is numeric type
                            if base_left_type in datetime_types and base_right_type in numeric_types and isinstance(right_node, LiteralNode):
                                # Convert integer literal to date-related expression
                                # Create TO_DATE function call node
                                to_date_func = Function('TO_DATE', 'date', ['string', 'string'])
                                to_date_node = FunctionCallNode(to_date_func)
                                
                                # Add TO_DATE parameters
                                to_date_node.add_child(LiteralNode('2023-01-01', 'DATE'))
                                to_date_node.add_child(LiteralNode('YYYY-MM-DD', 'STRING'))
                                
                                # Create + arithmetic expression node
                                plus_node = ArithmeticNode('+')
                                plus_node.add_child(to_date_node)
                                
                                # Create INTERVAL expression
                                interval_literal = LiteralNode(f'{right_node.value} days', 'STRING')
                                plus_node.add_child(interval_literal)
                                
                                # Replace the original integer literal
                                self.children[i] = plus_node
                            
                            # Conversion between numeric and string types
                            elif ((base_left_type in numeric_types and base_right_type in string_types) or 
                                  (base_right_type in numeric_types and base_left_type in string_types)) and isinstance(right_node, LiteralNode):
                                # Convert string type to numeric type
                                cast_func = Function('CAST', 'integer', ['string'])
                                cast_node = FunctionCallNode(cast_func)
                                cast_node.add_child(right_node)
                                self.children[i] = cast_node
                        else:
                            # For other dialects like MySQL, TIDB, perform basic type conversion
                            # Try to create a literal compatible with the left type
                            if isinstance(right_node, LiteralNode):
                                try:
                                    # Try to create a new literal based on the left type
                                    if base_left_type == 'INT':
                                        new_value = int(right_node.value)
                                        self.children[i] = LiteralNode(new_value, 'INT')
                                    elif base_left_type in ['FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC']:
                                        new_value = float(right_node.value)
                                        self.children[i] = LiteralNode(new_value, 'FLOAT')
                                    elif base_left_type in ['VARCHAR', 'TEXT', 'CHAR']:
                                        new_value = str(right_node.value)
                                        self.children[i] = LiteralNode(new_value, 'VARCHAR')
                                    elif base_left_type in ['DATE', 'DATETIME', 'TIMESTAMP']:
                                        # Simply try to convert to a date string
                                        self.children[i] = LiteralNode('2023-01-01', base_left_type)
                                except:
                                    # If conversion fails, use a safe default value
                                    if base_left_type in ['INT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC']:
                                        self.children[i] = LiteralNode(0, 'INT')
                                    elif base_left_type in ['VARCHAR', 'TEXT', 'CHAR']:
                                        self.children[i] = LiteralNode('default_value', 'VARCHAR')
                                    elif base_left_type in ['DATE', 'DATETIME', 'TIMESTAMP']:
                                        self.children[i] = LiteralNode('2023-01-01', base_left_type)

        # Handle type compatibility for regular binary comparison operators
        if self.operator in ['=', '<>', '!=', '<', '>', '<=', '>='] and len(self.children) >= 2:
            left_type = self._get_node_type(self.children[0])
            right_type = self._get_node_type(self.children[1])
            
            # Check if the types of left and right sides are compatible
            if left_type and right_type and not self._is_type_compatible(left_type, right_type):
                # Get base types
                base_left_type = left_type.split('(')[0].upper() if left_type else ''
                base_right_type = right_type.split('(')[0].upper() if right_type else ''
                
                # Handle comparison between datetime and numeric types
                datetime_types = {'DATE', 'DATETIME', 'TIMESTAMP'}
                numeric_types = {'INT', 'INTEGER', 'BIGINT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}
                
                # If left side is datetime type and right side is numeric type
                if base_left_type in datetime_types and base_right_type in numeric_types and isinstance(self.children[1], LiteralNode):
                    # Convert right integer literal to datetime-related expression
                    from data_structures.db_dialect import get_dialect_config
                    dialect = get_dialect_config()
                    
                    if dialect.name == 'POSTGRESQL':
                        from data_structures.function import Function
                        
                        # Create TO_DATE function call node
                        to_date_func = Function('TO_DATE', 'date', ['string', 'string'])
                        to_date_node = FunctionCallNode(to_date_func)
                        
                        # Add TO_DATE parameters
                        to_date_node.add_child(LiteralNode('2023-01-01', 'DATE'))
                        to_date_node.add_child(LiteralNode('YYYY-MM-DD', 'STRING'))
                        
                        # Create + arithmetic expression node
                        plus_node = ArithmeticNode('+')
                        plus_node.add_child(to_date_node)
                        
                        # Create INTERVAL expression
                        interval_literal = LiteralNode(f'{self.children[1].value} days', 'STRING')
                        plus_node.add_child(interval_literal)
                        
                        # Replace the original integer literal
                        self.children[1] = plus_node
                
                # If right side is datetime type and left side is numeric type
                elif base_right_type in datetime_types and base_left_type in numeric_types and isinstance(self.children[0], LiteralNode):
                    # Convert left integer literal to datetime-related expression
                    from data_structures.db_dialect import get_dialect_config
                    dialect = get_dialect_config()
                    
                    if dialect.name == 'POSTGRESQL':
                        from data_structures.function import Function
                        
                        # Create TO_DATE function call node
                        to_date_func = Function('TO_DATE', 'date', ['string', 'string'])
                        to_date_node = FunctionCallNode(to_date_func)
                        
                        # Add TO_DATE parameters
                        to_date_node.add_child(LiteralNode('2023-01-01', 'DATE'))
                        to_date_node.add_child(LiteralNode('YYYY-MM-DD', 'STRING'))
                        
                        # Create + arithmetic expression node
                        plus_node = ArithmeticNode('+')
                        plus_node.add_child(to_date_node)
                        
                        # Create INTERVAL expression
                        interval_literal = LiteralNode(f'{self.children[0].value} days', 'STRING')
                        plus_node.add_child(interval_literal)
                        
                        # Replace the original integer literal
                        self.children[0] = plus_node
                
                # Handle comparison between integer and string types
                elif (base_left_type in numeric_types and base_right_type in ['VARCHAR', 'TEXT', 'CHAR']) or\
                     (base_right_type in numeric_types and base_left_type in ['VARCHAR', 'TEXT', 'CHAR']):
                    # Detect comparison between integer and string types
                    from data_structures.db_dialect import get_dialect_config
                    dialect = get_dialect_config()
                    
                    if dialect.name == 'POSTGRESQL':
                        # In PostgreSQL, add explicit type conversion
                        from data_structures.function import Function
                        
                        # Right side is string type, left side is numeric type
                        if base_left_type in numeric_types and base_right_type in ['VARCHAR', 'TEXT', 'CHAR']:
                            # Convert right side string type to integer type
                            cast_func = Function('CAST', 'integer', ['string'])
                            cast_node = FunctionCallNode(cast_func)
                            cast_node.add_child(self.children[1])
                            self.children[1] = cast_node
                        # Left side is string type, right side is numeric type
                        elif base_right_type in numeric_types and base_left_type in ['VARCHAR', 'TEXT', 'CHAR']:
                            # Convert left side string type to integer type
                            cast_func = Function('CAST', 'integer', ['string'])
                            cast_node = FunctionCallNode(cast_func)
                            cast_node.add_child(self.children[0])
                            self.children[0] = cast_node