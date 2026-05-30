import sqlglot
import copy
import os
from get_seedQuery import SeedQueryGenerator
from generate_random_sql import get_tables

class ValueMutator:
    """Class for specifically identifying and mutating value expressions"""


    def __init__(self, ast):
        self.ast = ast
        self.aggregate_nodes = []  # Store identified aggregate function nodes
        self.mutated = False  # Flag indicating whether mutation has occurred
        self.comparison_results = []  # Store comparison results
        self.node_info_stack = []  # Stack data structure for storing node_info
        self.current_set_operation_node = None  # Store the currently found set operation node
        # Add table alias dependency graph related attributes
        self.alias_map = {}  # Store mapping relationships from aliases to actual references
        self.tables = get_tables()  # Import global table structure information
        # Build alias map
        self._build_alias_map(ast)
        
    def extract_rows(self, result_data):
        """Extract row data from query results"""
        # Adapt to format: (((20, 1),), ['main_col_1', 'main_col_2'])
        if isinstance(result_data, tuple) and len(result_data) == 2:
            rows = result_data[0]
            # Handle single or multiple row results
            if isinstance(rows, tuple) and len(rows) > 0 and isinstance(rows[0], tuple):
                return rows  # Multiple row results
            else:
                return (rows,)  # Single row result wrapped as tuple
        return ()
        
    def _find_window_functions_in_select(self, select_expr):
        """Find window function column indices in SELECT expressions"""
        window_columns = []
        for i, expr in enumerate(select_expr.expressions):
            # Check if it's a window function
            if expr.find(sqlglot.expressions.Window):
                window_columns.append(i)
            elif isinstance(expr, sqlglot.expressions.Alias):
                if isinstance(expr.this, sqlglot.expressions.Anonymous) and hasattr(expr.this, 'this') and isinstance(expr.this.this, str):
                    window_funcs = ['row_number', 'rank', 'dense_rank', 'ntile', 'lead', 'lag']
                    if any(func in expr.this.this.lower() for func in window_funcs):
                        window_columns.append(i)
        return window_columns
        
    def _find_all_window_functions(self, ast):
        """Recursively find all window function column indices in AST, supporting complex SQL structures"""
        window_columns = []
        
        # Handle SELECT statements
        if hasattr(ast, '__class__') and ast.__class__.__name__ == 'Select':
            return self._find_window_functions_in_select(ast)
        
        # Handle UNION/UNION ALL operations - use type name check instead of direct type checking
        elif hasattr(ast, '__class__') and 'Union' in ast.__class__.__name__:
            # For UNION operations, assume left and right sides have the same column structure, only need to analyze left SELECT
            if hasattr(ast, 'this'):
                return self._find_all_window_functions(ast.this)
        
        # Handle nested queries
        elif hasattr(ast, 'args'):
            for arg_name, arg_value in ast.args.items():
                if isinstance(arg_value, list):
                    for item in arg_value:
                        if hasattr(item, 'args'):
                            sub_result = self._find_all_window_functions(item)
                            if sub_result:
                                return sub_result
                elif hasattr(arg_value, 'args'):
                    sub_result = self._find_all_window_functions(arg_value)
                    if sub_result:
                        return sub_result
        
        return window_columns
        
    def rows_match_excluding_window_cols(self, row1, row2, sql):
        """Compare two rows for equality excluding window function columns"""
        # Parse SQL to AST to find window function columns
        window_function_columns = []
        try:
            # Parse SQL to AST
            ast = sqlglot.parse_one(sql)
            
            # Recursively find all window function columns
            window_function_columns = self._find_all_window_functions(ast)
        except Exception as e:
            return row1 == row2  # If parsing fails, fallback to complete comparison
        
        # Compare non-window function columns
        for col_idx in range(len(row1)):
            if col_idx not in window_function_columns:
                if row1[col_idx] != row2[col_idx]:
                    return False
        return True
        
    def extract_value(self, row_data, index):
        """Extract value at specific index from row data"""
        # Ensure input is a tuple
        if not isinstance(row_data, tuple):
            return None
        
        # If row has enough elements, directly return value at corresponding index
        if len(row_data) > index:
            return row_data[index]
        
        # Check if there are nested tuples to extract
        for item in row_data:
            if isinstance(item, tuple):
                nested_value = self.extract_value(item, index)
                if nested_value is not None:
                    return nested_value
        
        return None
        
    def _build_alias_map(self, ast):
        """Build mapping relationship from aliases to actual columns"""
        if not ast:
            return
        
        # Traverse AST to collect alias information
        self._collect_aliases(ast)
    
    def _collect_aliases(self, node):
        """Recursively collect alias information"""
        if not node:
            return
        
        # Check if it's an alias node
        if hasattr(node, 'alias') and getattr(node, 'alias', None):
            if hasattr(node, 'this'):
                # Get string representation of original expression
                original_expr = str(node.this)
                alias = getattr(node, 'alias').lower()
                # Map alias (lowercase) to original expression
                self.alias_map[alias] = original_expr
        
        # Recursively process child nodes
        if hasattr(node, 'args'):
            for child in node.args.values():
                if isinstance(child, (list, tuple)):
                    for item in child:
                        if hasattr(item, 'args'):
                            self._collect_aliases(item)
                elif hasattr(child, 'args'):
                    self._collect_aliases(child)
    
    def _get_column_info(self, column_name):
        """Get column information, including type and whether it's numeric"""
        # Convert column name to lowercase for case-insensitive comparison
        column_name_lower = column_name.lower()
        
        # Check and remove distinct keyword
        if 'distinct' in column_name_lower:
            # Extract actual column name after distinct
            column_name_lower = column_name_lower.replace('distinct', '').strip()
        
        # Initialize column_to_check variable
        column_to_check = column_name_lower
        # Check alias mapping
        if self.alias_map:
            # Handle table.column format column names, first look up table alias
            if '.' in column_name_lower:
                table_part, column_part = column_name_lower.split('.', 1)
                
                # First look up table alias
                if table_part in self.alias_map:
                    actual_table = self.alias_map[table_part]
                    
                    # Build column name with actual table name
                    aliased_column = f"{actual_table}.{column_part}"
                    column_to_check = aliased_column
                
                # Then check if the complete table.column format has an alias
                if column_to_check in self.alias_map:
                    actual_column = self.alias_map[column_to_check]
                    column_to_check = actual_column
            
            # If previous processing didn't find or it's a simple column name, check the entire column name
            elif column_name_lower in self.alias_map:
                actual_column = self.alias_map[column_name_lower]
                column_to_check = actual_column
        
        # Get column information
        column_info = {
            'name': column_name,
            'type': None,
            'has_nulls': False,
            'is_numeric': False
        }
        # Get column type from global table structure information
        if self.tables:
            found = False
            # First check if column_to_check contains table name
            target_table = None
            target_column = column_to_check
            
            if '.' in column_to_check:
                # Split into table.column
                table_part, column_part = column_to_check.split('.', 1)
                
                target_table = table_part
                target_column = column_part
            else:
                target_column = column_to_check
            
            # First, try to find the column in the specified table
            if target_table:
                for table in self.tables:
                    if table.name.lower() == target_table:
                        for column in table.columns:
                            if column.name.lower() == target_column:
                                column_info['type'] = column.data_type
                                column_info['has_nulls'] = column.is_nullable
                                column_info['is_numeric'] = column.category == 'numeric'
                                found = True
                                break
                        break
            
            # If not found in the specified table or no table specified, search globally
            if not found:
                for table in self.tables:
                    for column in table.columns:
                        if column.name.lower() == column_to_check or (target_column and column.name.lower() == target_column):
                            column_info['type'] = column.data_type
                            column_info['has_nulls'] = column.is_nullable
                            column_info['is_numeric'] = column.category == 'numeric'
                            found = True
                            break
                    if found:
                        break
        return column_info

    def _make_comparable(self, value):
        """Convert value to comparable type to ensure consistent types in sort keys"""
        import math
        from decimal import Decimal
        from datetime import timedelta
        # To ensure type consistency, we use tuples of type identifier + value as sort keys
        # This guarantees sorting by type first, then by value
        
        # Handle None values
        if value is None:
            return (0, -float('inf'))
        # Handle datetime.timedelta type
        elif isinstance(value, timedelta):
            # Convert timedelta to total seconds for comparison
            return (1, value.total_seconds())
        # Handle numeric types (including Decimal)
        elif isinstance(value, (int, float, Decimal)):
            try:
                num_value = float(value)
                # Handle NaN values
                if isinstance(num_value, float) and math.isnan(num_value):
                    return (2, float('inf'))
                return (1, num_value)
            except:
                return (4, str(value))
        # Handle bytes type
        elif isinstance(value, bytes):
            # For binary data, use byte lexicographical order for comparison (following MariaDB comparison rules)
            # Python bytes objects are compared lexicographically by default
            try:
                value = str(value)[2:]
                value = float(value)
                value = round(value, 2)
                return (2, value)
            except:
                return (4, value)
        # Handle string type
        elif isinstance(value, str):
            # Convert string to lowercase for comparison
            return (3, value.lower())
        # Handle other types
        else:
            try:
                return (4, str(value))
            except:
                return (5, id(value))
                
    def _get_function_param_info(self, func_node):
        """Get information about function parameters, including whether they are numeric"""
        # Get function name
        func_name = func_node.__class__.__name__
        
        # Initialize parameter information
        param_info = {
            'is_numeric': False,
            'param_str': '',
            'param_type': None
        }
        
        # Try to get function parameters
        if hasattr(func_node, 'this') and func_node.this:
            # For COUNT(DISTINCT) cases
            if func_name == 'Count' and func_node.this.__class__.__name__ == 'Distinct':
                if hasattr(func_node.this, 'expressions') and func_node.this.expressions:
                    param_str = str(func_node.this.expressions[0])
                    param_info['param_str'] = 'DISTINCT ' + param_str
                    # Get column info for distinct parameter
                    column_info = self._get_column_info(param_str)
                    param_info['is_numeric'] = column_info['is_numeric']
                    param_info['param_type'] = column_info['type']
            else:
                # Regular parameter case
                param_str = str(func_node.this)
                param_info['param_str'] = param_str
                # Get column info for parameter
                column_info = self._get_column_info(param_str)
                param_info['is_numeric'] = column_info['is_numeric']
                param_info['param_type'] = column_info['type']
        return param_info
    
    def get_sort_key(self, row, mutated_columns, sql):
        """Generate sort key with safe handling of None values and various data types"""
        from decimal import Decimal
        import math
        # Parse SQL to AST to find window function columns
        window_function_columns = []
        try:
            # Parse SQL to AST
            ast = sqlglot.parse_one(sql)
            
            # Recursively find all window function columns
            window_function_columns = self._find_all_window_functions(ast)
        except Exception as e:
            pass
            
        # Create sort key: non-target columns and non-window function columns first, then target columns
        key = []
        for col_idx, col_value in enumerate(row):
            for mutated_column in mutated_columns:
                if col_idx != mutated_column['index'] and col_idx not in window_function_columns:
                    key.append(self._make_comparable(col_value))
        # Add target column values to the end of the sort key
        for mutated_column in mutated_columns:
            if 0 <= mutated_column['index'] < len(row):
                target_value = row[mutated_column['index']]
                key.append(self._make_comparable(target_value))
        return key
        
    def generate_signature(self, row, index, mutated_columns, sql):
        """Generate row signature for comparison"""
        signature = []
        window_function_columns = []
        try:
            # Parse SQL to AST to find window function columns
            ast = sqlglot.parse_one(sql)
            
            # Recursively find all window function columns
            window_function_columns = self._find_all_window_functions(ast)
        except Exception as e:
            pass
        
        # Add values from non-target columns and non-window function columns
        in_mutated_index=False
        for col_idx, col_value in enumerate(row):
            for mutated_column in mutated_columns:
                if col_idx == mutated_column['index']:
                    in_mutated_index=True
                    break
            if not in_mutated_index and col_idx not in window_function_columns:
                    # Handle approximate equality for all numeric types (including Decimal) to two decimal places
                try:
                        # Handle bytes type
                        if isinstance(col_value, bytes):
                            # Use bytes object directly for signature, maintaining byte lexicographical order (following MariaDB comparison rules)
                            signature.append(col_value)
                        # Check if it's a numeric type
                        elif isinstance(col_value, (int, float, Decimal)):
                            # Convert to float and round to two decimal places
                            rounded_value = round(float(col_value), 2)
                            signature.append(rounded_value)
                        # Add handling for string type numbers
                        elif isinstance(col_value, str):
                            try:
                                # Try to convert string to float
                                float_value = float(col_value)
                                # Round to two decimal places
                                rounded_value = round(float_value, 2)
                                signature.append(rounded_value)
                            except ValueError:
                                # If cannot convert to numeric, keep original string
                                signature.append(col_value)
                        elif 'Decimal' in str(type(col_value)):
                            # Special handling for Decimal type using Decimal's quantize method for precise rounding
                            try:
                                from decimal import Decimal, ROUND_HALF_UP
                                # Ensure col_value is Decimal type
                                if not isinstance(col_value, Decimal):
                                    decimal_value = Decimal(str(col_value))
                                else:
                                    decimal_value = col_value
                                # Round to two decimal places using ROUND_HALF_UP mode
                                rounded_decimal = decimal_value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                                # Convert to float and add to signature
                                rounded_value = float(rounded_decimal)
                                signature.append(rounded_value)
                            except:
                                # If using Decimal's quantize method fails, fall back to basic conversion
                                float_value = float(str(col_value))
                                rounded_value = round(float_value, 2)
                                signature.append(rounded_value)
                        else:
                            # Non-numeric types are added directly
                            signature.append(col_value)
                except:
                        # If conversion fails, use the original value
                        signature.append(col_value)
        
        # Add index identifier, but do not distinguish between original and mutated data
        signature.append(f"row_{index}")
        return signature

    def find_aggregate_nodes(self,ast):
        """Identify all aggregate function nodes in the AST"""
        if not ast:
            return

        # Clear previous results
        self.aggregate_nodes = []
        self._find_aggregate_nodes(ast)
        return self.aggregate_nodes

    def _find_aggregate_nodes(self, node):
        """Recursively find aggregate function nodes, regular columns, and scalar function nodes"""
        
        if not node:
            return

        # Check if current node is an aggregate function, regular column, or scalar function
        if self._is_aggregate_function(node) or self._is_regular_column(node) or self._is_scalar_function(node):
            if node.parent.__class__.__name__ == 'Alias' and node == node.parent.this:
            # Record node information
                self._add_aggregate_node(node)

        # Recursively process child nodes
        if hasattr(node, 'args'):
            for child in node.args.values():
                if isinstance(child, (list, tuple)):
                    for item in child:
                        if hasattr(item, 'args'):
                            self._find_aggregate_nodes(item)
                elif hasattr(child, 'args'):
                    self._find_aggregate_nodes(child)

    def _is_regular_column(self, node):
        """
        Determine if a node is a regular column (Column type)
        """
        try:
            return hasattr(node, '__class__') and node.__class__.__name__ == 'Column'
        except:
            return False
    
    def _is_scalar_function(self, node):
        """
        Determine if a node is a scalar function (non-aggregate, non-window function)
        """
        try:
            if not hasattr(node, '__class__'):
                return False
            
            # Not aggregate or window function, but has function characteristics
            return (not self._is_aggregate_function(node) and 
                    not self._is_window_function(node) and 
                    hasattr(node, '__class__'))
        except:
            return False
    
    def _is_window_function(self, node):
        """
        Determine if a node is a window function
        """
        try:
            # Check if there is a window clause (over clause)
            if hasattr(node, 'args') and 'over' in node.args and node.args['over']:
                return True
            return False
        except:
            return False
    
    def _is_aggregate_function(self, node):
        """Determine if a node is an aggregate function"""
        # List of aggregate function types, based on class names actually supported by sqlglot
        aggregate_functions = [
            'Avg', 'Count', 'Max', 'Min', 'Sum',
            'GroupConcat',
            'Std', 'Stddev', 'StddevPop', 'StddevSamp',
            'Variance', 'VariancePop',
            'BitAnd', 'BitOr', 'BitXor'
        ]

        # Check if node type name is an aggregate function
        if hasattr(node, '__class__'):
            class_name = node.__class__.__name__
            result = class_name in aggregate_functions
            
            # Special case 1: VarSamp is represented by Variance class in sqlglot
            if not result and class_name == 'Variance' and hasattr(node, 'samp') and node.samp is True:
                result = True
            
            # Special case 2: Handle Anonymous type aggregate functions (like STD function)
            if not result and class_name == 'Anonymous':
                # Check if function name is an aggregate function
                if hasattr(node, 'name') and node.name is not None:
                    func_name = node.name
                    if func_name.upper() in ['STD', 'STDDEV', 'VAR', 'VARIANCE', 'BIT_AND', 'BIT_OR', 'BIT_XOR']:
                        result = True
                elif hasattr(node, 'this') and isinstance(node.this, str):
                    # In some cases, the this attribute is a function name in string form
                    func_name = node.this
                    if func_name.upper() in ['STD', 'STDDEV', 'VAR', 'VARIANCE', 'BIT_AND', 'BIT_OR', 'BIT_XOR']:
                        result = True
            
            return result
        return False

    def _add_aggregate_node(self, node):
        """Add aggregate function node to the list"""
        # Generate unique identifier
        node_id = id(node)

        # Get function name
        func_name = node.__class__.__name__
        
        # Special handling for Anonymous type nodes, get function name from parameters
        if func_name == 'Anonymous':
            if hasattr(node, 'name') and node.name is not None:
                func_name = node.name.upper()
                # Map BIT_AND, BIT_OR, BIT_XOR to corresponding class name format
                if func_name == 'BIT_AND':
                    func_name = 'BitAnd'
                elif func_name == 'BIT_OR':
                    func_name = 'BitOr'
                elif func_name == 'BIT_XOR':
                    func_name = 'BitXor'
            elif hasattr(node, 'this') and isinstance(node.this, str):
                func_name = node.this.upper()
                # Map BIT_AND, BIT_OR, BIT_XOR to corresponding class name format
                if func_name == 'BIT_AND':
                    func_name = 'BitAnd'
                elif func_name == 'BIT_OR':
                    func_name = 'BitOr'
                elif func_name == 'BIT_XOR':
                    func_name = 'BitXor'
        
        # Get path list (from root node to current node)
        path_list = self._get_node_path_list(node)
        
        # When encountering a WITH query, get the alias of the query containing this aggregate function node in the main query; if it does not appear in the main query, record it as None
        cte_alias = None
        outer_alias = []  # Changed to list to support multiple outer aliases
        
        # Check if it's a WITH query
        if 'With' in path_list:
            # Get CTE alias
            try:
                # Search up for CTE node
                current_node = node
                while current_node:
                    if hasattr(current_node, '__class__') and current_node.__class__.__name__ == 'CTE' and hasattr(current_node, 'alias'):
                        cte_alias = current_node.alias
                        break
                    if hasattr(current_node, 'parent'):
                        current_node = current_node.parent
                    else:
                        break
                
                # Try to get outer alias (if CTE is aliased again in the outer level)
                if cte_alias:
                    try:
                        # Find root node
                        root_node = current_node  # At this point, current_node is already a CTE node or its ancestor
                        while root_node and hasattr(root_node, 'parent') and root_node.parent:
                            root_node = root_node.parent
                        
                        # Start from the root node to find the FROM clause of the main query
                        if root_node:
                            # Traverse all child nodes of the root node
                            for child in root_node.args.values():
                                if isinstance(child, (list, tuple)):
                                    for item in child:
                                        if hasattr(item, '__class__') and item.__class__.__name__ == 'From':
                                            # Find table references in the FROM clause that reference the CTE alias
                                            for table_ref in item.args.values():
                                                if isinstance(table_ref, (list, tuple)):
                                                    for ref in table_ref:
                                                        if (hasattr(ref, '__class__') and 
                                                            hasattr(ref, 'args') and 
                                                            'this' in ref.args and 
                                                            hasattr(ref.args['this'], '__class__') and 
                                                            ref.args['this'].__class__.__name__ == 'Identifier' and 
                                                            hasattr(ref.args['this'], 'this') and 
                                                            ref.args['this'].this == cte_alias and 
                                                            'alias' in ref.args and 
                                                            ref.args['alias']):
                                                            if ref.args['alias'] not in outer_alias:
                                                                outer_alias.append(ref.args['alias']) 
                            # If not found in the FROM clause of the main query, continue searching elsewhere
                            if not outer_alias:
                                # Traverse the entire root node tree
                                for descendant in root_node.walk():
                                    if (hasattr(descendant, '__class__') and 
                                        hasattr(descendant, 'args') and 
                                        'this' in descendant.args and 
                                        hasattr(descendant.args['this'], '__class__') and 
                                        descendant.args['this'].__class__.__name__ == 'Identifier' and 
                                        hasattr(descendant.args['this'], 'this') and 
                                        descendant.args['this'].this == cte_alias and 
                                        'alias' in descendant.args and 
                                        descendant.args['alias']):
                                        if descendant.args['alias'] not in outer_alias:
                                            outer_alias.append(descendant.args['alias'])  # Collect all matching aliases instead of breaking
                    except Exception as e:
                        print(f"Error obtaining outer alias through AST traversal: {str(e)}")
            except Exception as e:
                print(f"Error obtaining WITH query alias: {str(e)}")
        
        # Try to get column alias
        column_alias = None
        # Check if parent node is an Alias node
        if hasattr(node, 'parent') and node.parent:
            parent_class = node.parent.__class__.__name__
            if parent_class == 'Alias' and hasattr(node.parent, 'alias'):
                column_alias = node.parent.alias
        
        # Check if node is in a subquery and get subquery alias
        subquery_alias = None
        try:
            # Search up for subquery node (Subquery type)
            current_node = node
            while current_node:
                if hasattr(current_node, '__class__') and current_node.__class__.__name__ == 'Subquery':
                    # Check if subquery has an alias
                    if hasattr(current_node, 'alias'):
                        subquery_alias = current_node.alias
                        break
                if hasattr(current_node, 'parent'):
                    current_node = current_node.parent
                else:
                    break
        except Exception as e:
            print(f"Error obtaining subquery alias: {str(e)}")
            subquery_alias = None

        # Determine node type
        node_type = 'aggregate_function'  # Default is aggregate function
        if self._is_regular_column(node):
            node_type = 'regular_column'
            # For regular columns, use the column name as func_name
            if hasattr(node, 'this'):
                func_name = node.this
        elif self._is_scalar_function(node):
            node_type = 'scalar_function'
        
        # Build node information, add position information
        node_info = {
            'node_id': node_id,
            'node': node,
            'func_name': func_name,
            'node_type': node_type,  # Add node type
            'parent_type': self._get_parent_type(node),
            'column_alias': column_alias,  # Add column alias information
            'node_path': self._get_node_path(node),
            'node_path_list': path_list,  # Path list from root node to current node
            'node_path_list_reversed': list(reversed(path_list)),  # Reversed path list (from current node to root node)
            'direction': None,  # Record mutation direction
            'cte_alias': cte_alias,  # CTE alias in WITH query
            'outer_alias': outer_alias,  # List of aliases when CTE is referenced again in outer levels
            'subquery_alias': subquery_alias  # Subquery alias
        }
        
        self.aggregate_nodes.append(node_info)

    def _preprocess_ast(self, ast, target_index, original_nodes=None):
        """Preprocess AST: replace all aggregate function nodes except the target one with their arguments, 
        remove limit nodes from all select nodes, and remove distinct attribute from all queries
        
        Parameters:
            ast: The AST to process
            target_index: Index of the target aggregate function node to keep (starting from 0)
            original_nodes: Optional, original list of aggregate function nodes. If provided, use directly to avoid re-finding
        """
        
        # Use the provided original node information to avoid ID changes caused by re-finding
        if original_nodes:
            all_aggregate_nodes = original_nodes
        else:
            # First find all aggregate function nodes in the original AST (without using temporary objects)
            self.find_aggregate_nodes(ast)
            all_aggregate_nodes = self.aggregate_nodes
        
        # Check if target index is valid
        if 0 <= target_index < len(all_aggregate_nodes):
            target_node_info = all_aggregate_nodes[target_index]
            # Iterate through all aggregate function nodes and replace non-target nodes
            replaced_count = 0
            for idx, node_info in enumerate(all_aggregate_nodes):
                # Use index to determine if it's the target node
                if idx != target_index:
                    temp_node = node_info['node']
                    in_alias_subquery = False
                    select_node_ = self._find_nearest_select_node(temp_node)
                    if select_node_ and select_node_.parent.__class__.__name__ == 'Subquery' and select_node_.parent.parent.__class__.__name__ == 'Alias':
                        in_alias_subquery = True
                    # Replace current aggregate function node with its argument, handling DISTINCT cases
                    self._replace_aggregate_with_argument(node_info['node'], ast)
                    replaced_count += 1
                    # Check if the query containing this node is a subquery node under Alias

                    # If it's a subquery node under Alias, add limit 1
                    if in_alias_subquery:
                        # Search up for the nearest Select node
                        select_node = None
                        select_node = select_node_
                        # Add limit 1 and order by clause to the Select node
                        if select_node:
                            from sqlglot.expressions import Literal, Limit, Order, Select
                            # Add order by clause using sqlglot's approach
                            try:
                                # Get the first expression of the select node
                                first_expression = select_node.args['expressions'][0]
                                first_expression = first_expression.this
                                # Create Order object
                                order = Order(expressions=[first_expression])
                                # Add order by clause using sqlglot's approach
                                # Check if select node already has order_by attribute
                                # Add Order object to order_by list
                                select_node.args['order'] = order
                            except Exception as e:
                                print(f"Error adding order by clause using sqlglot approach: {e}")
                            # Add limit 1
                            limit = Limit(expression='1')
                            select_node.args['limit'] = limit
        
        # After preprocessing, find aggregate function nodes again to verify the result
        self.aggregate_nodes = []  # Clear previous results
        self.find_aggregate_nodes(ast)
        
        # Remove distinct attribute from all queries
        try:
            def remove_distinct_from_selects(node):
                # Check if current node is a Select node
                if isinstance(node, sqlglot.expressions.Select):
                    # Set distinct attribute to False
                    if hasattr(node, 'distinct'):
                        node.distinct = False
                    # Also check if 'distinct' exists in args
                    if 'distinct' in node.args:
                        node.args['distinct'] = False
                
                # Recursively process child nodes
                for arg_name, arg_value in node.args.items():
                    if isinstance(arg_value, sqlglot.expressions.Expression):
                        remove_distinct_from_selects(arg_value)
                    elif isinstance(arg_value, list):
                        for item in arg_value:
                            if isinstance(item, sqlglot.expressions.Expression):
                                remove_distinct_from_selects(item)
            
            # Apply recursive function to the entire AST
            remove_distinct_from_selects(ast)
        except Exception as e:
            print(f"Error removing distinct attribute: {str(e)}")
        
        # Remove limit nodes from all select nodes
        try:
            # Define recursive function to find and remove all limit clauses from select nodes
            def remove_limit_from_selects(node):
                # Check if current node is a Select node
                if isinstance(node, sqlglot.expressions.Select):
                    # Check if it's a Select node under Alias
                    in_alias = False
                    try:
                        # Search up for Alias parent node
                        temp_node = node
                        while temp_node:
                            if hasattr(temp_node, 'parent') and temp_node.parent:
                                parent_parent_class = temp_node.parent.parent.__class__.__name__
                                if parent_parent_class == 'Alias':
                                    in_alias = True
                                    break
                                temp_node = temp_node.parent
                            else:
                                break
                    except Exception as e:
                        print(f"Error checking if Select node is under Alias: {str(e)}")
                    
                    # For Select nodes not under Alias, remove limit clause
                    if not in_alias and 'limit' in node.args:
                        del node.args['limit']
                
                # Recursively process child nodes
                for arg_name, arg_value in node.args.items():
                    if isinstance(arg_value, sqlglot.expressions.Expression):
                        remove_limit_from_selects(arg_value)
                    elif isinstance(arg_value, list):
                        for item in arg_value:
                            if isinstance(item, sqlglot.expressions.Expression):
                                remove_limit_from_selects(item)
            
            # Apply recursive function to the entire AST
            remove_limit_from_selects(ast)
        except Exception as e:
            print(f"Error removing limit node: {e}")
        
        return ast
        
    def _find_nearest_select_node(self, node):
        """Find the nearest SELECT node (not necessarily the root node)
        
        Parameters:
            node: Current node
        
        Returns:
            The nearest SELECT node
        """
        current = node
        if not current.parent:
            return None
        while current and hasattr(current, 'parent'):
            # Check if current node is a SELECT node
            if hasattr(current, '__class__') and 'Select' in current.__class__.__name__:
                return current
            current = current.parent
        return None
        
    def _replace_aggregate_with_argument(self, aggregate_node, ast=None):
        """Replace an aggregate function node with its argument, handling cases with DISTINCT
        
        Parameters:
            aggregate_node: Aggregate function node
            ast: Optional, original AST for direct access to GROUP BY clause
        """
        try:
            # Get aggregate function type
            if aggregate_node.__class__.__name__ in ['Column', 'Identifier']:
                return
            func_type = aggregate_node.__class__.__name__
            argument_node = None
            
            # Handle Anonymous type aggregate function nodes (such as VAR_SAMP, BIT_OR, BIT_AND, STDDEV, etc.)
            if func_type == 'Anonymous' and hasattr(aggregate_node, 'expressions') and aggregate_node.expressions:
                # Get the first parameter from expressions list as argument node
                argument_node = aggregate_node.expressions[0]
            # Handle normal aggregate function nodes
            elif hasattr(aggregate_node, 'this') and aggregate_node.this:
                # Check if it's an aggregate function with DISTINCT
                if hasattr(aggregate_node.this, '__class__') and aggregate_node.this.__class__.__name__ == 'Distinct':
                    # Handle aggregate functions with DISTINCT (COUNT(DISTINCT), SUM(DISTINCT), AVG(DISTINCT), etc.)
                    if hasattr(aggregate_node.this, 'expressions') and aggregate_node.this.expressions:
                        # Get the expression inside DISTINCT
                        argument_node = aggregate_node.this.expressions[0]
                    else:
                        argument_node = aggregate_node.this
                else:
                    # Handle normal aggregate functions
                    argument_node = aggregate_node.this
            
            # If an argument node is found
            if argument_node:
                # Copy the argument node to avoid reference issues
                argument_copy = copy.deepcopy(argument_node)
                
                # Replace the aggregate function node with the argument node
                new_node = aggregate_node.replace(argument_copy)
                
                # Process GROUP BY clause: Check if parameter column is in GROUP BY clause, add if not present
                # Find the nearest SELECT node, not the root node, to ensure parameter columns in subqueries are added to their own GROUP BY clauses
                nearest_select_node = self._find_nearest_select_node(new_node) or self._find_nearest_select_node(aggregate_node)
                if nearest_select_node:
                    # Check if parameter column needs to be added to GROUP BY clause
                    self._ensure_in_group_by(nearest_select_node, argument_copy)
        except Exception as e:
            pass
    
    def _find_root_node(self, node):
        """Find the root node (SELECT statement)"""
        current = node
        while current and hasattr(current, 'parent'):
            current = current.parent
        return current
    
    def _ensure_in_group_by(self, select_node, column_node):
        """Ensure column is in GROUP BY clause, add if not present
        
        Parameters:
            select_node: SELECT statement node
            column_node: Column node
        """
        try:
            # Check if it's a constant node (LiteralNode), which doesn't need to be added to GROUP BY clause
            from sqlglot.expressions import Literal, Column
            if isinstance(column_node, Literal):
                return
            # Check if GROUP BY clause already exists
            if hasattr(select_node, 'args'):
                if 'group' in select_node.args and select_node.args['group']:
                    group_by_clause = select_node.args['group']
                    
                    # Check if column is already in GROUP BY clause
                    if self._is_column_in_group_by(group_by_clause, column_node):
                        return  # Column already in GROUP BY clause, no need to add
                    
                    # If column is not in GROUP BY clause, add it
                    if hasattr(group_by_clause, 'expressions'):
                        # Copy column node to avoid reference issues
                        column_copy = copy.deepcopy(column_node)
                        # Add to GROUP BY clause
                        group_by_clause.expressions.append(column_copy)
                else:
                    # If no GROUP BY clause exists, create a new one
                    from sqlglot.expressions import Group
                    # Create Group expression as GROUP BY clause
                    column_copy = copy.deepcopy(column_node)
                    group_by_expressions = [column_copy]
                    # Create Group object
                    new_group = Group(expressions=group_by_expressions)
                    # Set GROUP BY clause to SELECT node
                    select_node.args['group'] = new_group
        except Exception as e:
            pass  # Silently handle exceptions
    
    def _is_column_in_group_by(self, group_by_clause, column_node):
        """Check if column is in GROUP BY clause
        
        Parameters:
            group_by_clause: GROUP BY clause node
            column_node: Column node
        
        Returns:
            bool: Whether column is in GROUP BY clause
        """
        try:
            if not hasattr(group_by_clause, 'expressions'):
                return False
            
            # Convert column nodes to SQL strings for comparison
            column_sql = str(column_node)
            
            # Check each expression in GROUP BY clause
            for expr in group_by_clause.expressions:
                expr_sql = str(expr)
                if expr_sql == column_sql:
                    return True
            
            return False
        except Exception as e:
            return False
            
    def _find_closest_select_node(self, node):
        """Find the closest SELECT node by traversing upward from given node
        
        Parameters:
            node: Starting node
        
        Returns:
            Select node: Closest SELECT node, or None if not found
        """
        try:
            current = node
            # Traverse up the AST until finding a SELECT node or reaching the root node
            while current is not None:
                # Check if current node is a SELECT node
                if hasattr(current, '__class__') and hasattr(current.__class__, '__name__'):
                    if current.__class__.__name__ == 'Select':
                        return current
                # Continue traversing upwards
                if hasattr(current, 'parent') and current.parent is not None:
                    current = current.parent
                else:
                    break
            return None
        except Exception as e:
            pass  # Silently handle exceptions
            return None
            
    def _update_group_by_with_mutated_node(self, original_node, mutated_node):
        """Synchronously update nodes in GROUP BY clause
        
        Parameters:
            original_node: Original node
            mutated_node: Mutated node
        """
        try:
            
            # Get the nearest SELECT node
            select_node = self._find_nearest_select_node(mutated_node)
            if not select_node:
                return
            
            # Check if there is a GROUP BY clause
            if hasattr(select_node, 'args') and 'group' in select_node.args and select_node.args['group']:
                group_by_clause = select_node.args['group']
                
                if hasattr(group_by_clause, 'expressions'):
                    original_node_str = str(original_node)
                    
                    # Iterate through each expression in the GROUP BY clause
                    for i, expr in enumerate(group_by_clause.expressions):
                        expr_str = str(expr)
                        
                        # If matching expression is found, perform replacement
                        if expr_str == original_node_str:
                            # Add mutated node to GROUP BY expression list instead of replacing
                            group_by_clause.expressions.append(mutated_node)
                            break
        except Exception as e:
            pass  # Silently handle exceptions

    def mutate(self):
        """Perform mutation operations on aggregate functions"""
        if self.mutated or not self.aggregate_nodes:
            return self.ast


        from get_seedQuery import SeedQueryGenerator
        generator=SeedQueryGenerator()
        sql=str(self.ast)
        initial_ast=copy.deepcopy(self.ast)
        
        #Ensure only one node is mutated at a time
        self.find_aggregate_nodes(self.ast)
        init_mutatble_nodes=self.aggregate_nodes
        # Iterate through all aggregate function nodes
        #node_info contains node information from initial_ast
        for i,node_info in enumerate(init_mutatble_nodes):
            self.ast=copy.deepcopy(initial_ast)
            self.find_aggregate_nodes(self.ast)
            current_mutatble_nodes=self.aggregate_nodes
            # Preprocess AST: Replace all aggregate functions except the current node to be mutated with parameters
            target_index = i  # Use current loop index as target index
            # Pass original node list to avoid ID changes caused by re-finding
            self._preprocess_ast(self.ast, target_index, current_mutatble_nodes)
            sql=str(self.ast)
            initial_result=generator.execute_query(str(self.ast))
            # Re-find aggregate function nodes, there should only be one node to mutate at this point
            temp_ast=self.ast
            self.find_aggregate_nodes(temp_ast)
            
            # Find the current node to mutate
            node_info=current_mutatble_nodes[target_index]
            
            node = node_info['node']
            func_name = node_info['func_name']
            parent_type=node_info['parent_type']
            
            # Check node type, perform constant mutation for regular columns and scalar functions
            if node_info.get('node_type') == 'regular_column':
                # Get column information, check if it's numeric type
                column_name = str(node)
                column_info = self._get_column_info(column_name)
                if column_info['is_numeric']:
                    # Perform constant mutation for numeric type regular columns
                    node_info['direction'] = 1
                    mutated_node = self._mutate_regular_column_constant(node, 2)
                    node_info['node'] = mutated_node
                    
                    # Synchronously update corresponding column in GROUP BY clause
                    self._update_group_by_with_mutated_node(node, mutated_node)
            elif node_info.get('node_type') == 'scalar_function':
                # Get scalar function parameter information, check if it's numeric type
                param_info = self._get_function_param_info(node)
                if param_info['is_numeric']:
                    # Perform constant mutation for numeric type scalar functions
                    node_info['direction'] = 1
                    mutated_node = self._mutate_regular_column_constant(node, 2)
                    node_info['node'] = mutated_node
                    
                    # Synchronously update corresponding scalar function in GROUP BY clause
                    self._update_group_by_with_mutated_node(node, mutated_node)
            # Perform corresponding mutation based on aggregate function type
            elif func_name == 'Avg':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                # AVG(c1) => MAX(c1)
                import random
                rand_val = random.random()
                if param_info['is_numeric'] and rand_val < 0.25:
                    # If parameter is numeric type, 25% probability to perform +random mutation
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_add_positive_random(node)
                elif rand_val < 0.5:
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_avg_to_max(node)
                elif rand_val < 0.75:
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_monotonic_multiplication(node, 3)
                else:
                    # AVG(x) => AVG(x) + 2
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_avg_add_constant(node, 2)
            elif func_name == 'Min':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                if param_info['is_numeric'] and rand_val < 0.5:
                    # If parameter is numeric type, 50% probability to perform +random mutation
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_add_positive_random(node)
                else:
                    # MIN(c1) => MAX(c1)
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_min_to_max(node)
            elif func_name == 'Max':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                if param_info['is_numeric'] and rand_val < 0.5:
                    # If parameter is numeric type, 50% probability to perform +random mutation
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_add_positive_random(node)
                else:
                    # MAX(c1) => MIN(c1)
                    node_info['direction'] = 0
                    node_info['node']=self._mutate_max_to_min(node)
            elif func_name == 'Count':  
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                # Check if it's COUNT(DISTINCT c1)
                if self._is_count_distinct(node):
                    # COUNT(DISTINCT c1) has three mutation methods
                    if rand_val < 0.33:
                        # COUNT(DISTINCT c1) => COUNT(c1)
                        node_info['direction'] = 1
                        node_info['node']=self._mutate_count_distinct_to_count(node)
                    else:
                        # COUNT(DISTINCT c1) => COUNT(DISTINCT c1) + random
                        node_info['direction'] = 1
                        node_info['node']=self._mutate_add_positive_random(node)
                else:
                    # COUNT(c1) has three mutation methods
                    if rand_val < 0.33:
                        # COUNT(c1) => COUNT(DISTINCT c1)
                        node_info['direction'] = 0
                        node_info['node']= self._mutate_count_to_count_distinct(node)
                    else:
                        # COUNT(c1) => COUNT(c1) + random
                        node_info['direction'] = 1
                        node_info['node']=self._mutate_add_positive_random(node)

            elif func_name == 'Variance' or func_name == 'VarPop' or func_name == 'VarSamp':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                
                # VARIANCE(c) => STDDEV(c)
                node_info['direction'] = 0
                node_info['node']=self._mutate_variance_to_stddev(node)
            elif func_name == 'StddevSamp':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                
                # STDDEV_SAMP(c) => VAR_SAMP(c)
                node_info['direction'] = 1
                node_info['node']=self._mutate_stddev_to_variance(node)
            
            # Function monotonicity mutation
            elif func_name == 'Sum':
                # Get function parameter information
                param_info = self._get_function_param_info(node)
                import random
                rand_val = random.random()
                if param_info['is_numeric'] and rand_val < 0.5:
                    # If parameter is numeric type, 50% probability to perform +random mutation
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_add_positive_random(node)
                else:
                    # SUM(c) => SUM(c*2) maintaining monotonicity
                    node_info['direction'] = 1
                    node_info['node']=self._mutate_monotonic_multiplication(node, 2)
            node_to_direction=node_info
            mutate_sql=str(self.ast)
            mutate_result=generator.execute_query(mutate_sql)
            mutated_ast=sqlglot.parse_one(mutate_sql)
            self.find_aggregate_nodes(mutated_ast)
            for temp_node in self.aggregate_nodes:
                pass
            node_info['direction']=node_to_direction['direction']
            # Get root node of node in node_info
            current_node = node_info['node']
            root_node = None
            root_node=current_node.root()
            # Call result comparison function
            comparison_result = self.compare_results(initial_result, mutate_result, node_info, sql, root_node)
            
            # Check if current mutation needs to be terminated
            if comparison_result.get('terminate_mutation'):
                # Skip directly to next node processing
                continue
                 
            # Add comparison result to result list
            if comparison_result['is_expected'] ==False:
                # Record unexpected mutations, preferring sorted results
                original_result_to_log = comparison_result.get('sorted_original_result', initial_result)
                mutated_result_to_log = comparison_result.get('sorted_mutated_result', mutate_result)
                
                self._log_invalid_mutation(
                    original_sql=sql,
                    mutated_sql=str(self.ast),
                    original_result=original_result_to_log,
                    mutated_result=mutated_result_to_log,
                    node_info=node_info,
                    reason="Aggregate function mutation result does not meet expectations"
                )

            
        self.mutated = True
        return self.ast

    def _is_count_distinct(self, count_node):
        """Check if COUNT function uses DISTINCT"""
        # In sqlglot, COUNT(DISTINCT) is represented as a Count node containing a Distinct node
        if hasattr(count_node, 'this') and count_node.this:
            return count_node.this.__class__.__name__ == 'Distinct'
        return False

    def _mutate_avg_to_max(self, avg_node):
        """Mutate AVG function to MAX function"""
        try:
            # Get AVG function parameters
            args = copy.deepcopy(avg_node.args)
            
            # Create new MAX function node
            max_node = sqlglot.expressions.Max(**args)
            
            # Replace node
            avg_node.replace(max_node)
            return max_node
        except Exception as e:
            pass  # Silently handle exceptions

    def _mutate_avg_add_constant(self, avg_node, constant_value):
        """Mutate AVG function to AVG function plus constant: AVG(x) => AVG(x) + constant"""
        try:
            from sqlglot.expressions import Add, Literal
            
            # Create constant literal
            constant_literal = Literal(this=str(constant_value), is_string=False)
            
            # Create addition expression: AVG(x) + constant
            add_expression = Add(this=avg_node.copy(), expression=constant_literal)
            
            # Directly replace AVG node
            avg_node.replace(add_expression)
            return add_expression
        except Exception as e:
            pass  # Silently handle exceptions
            
    def _mutate_count_add_constant(self, count_node, constant_value):
        """Mutate COUNT function to COUNT function plus constant: COUNT(x) => COUNT(x) + constant"""
        try:
            from sqlglot.expressions import Add, Literal
            
            # Create constant literal
            constant_literal = Literal(this=str(constant_value), is_string=False)
            
            # Create addition expression: COUNT(x) + constant
            add_expression = Add(this=count_node.copy(), expression=constant_literal)
            
            # Directly replace COUNT node
            count_node.replace(add_expression)
            
            return add_expression
        except Exception as e:
            pass  # Silently handle exceptions
            
    def _mutate_min_to_max(self, min_node):
        """Mutate MIN function to MAX function"""
        try:
            # Get MIN function parameters
            args = copy.deepcopy(min_node.args)
            
            # Create new MAX function node
            max_node = sqlglot.expressions.Max(**args)
            
            # Replace node
            min_node.replace(max_node)
            return max_node
        except Exception as e:
            pass  # Silently handle exceptions

    def _mutate_max_to_min(self, max_node):
        """Mutate MAX function to MIN function"""
        try:
            # Get MAX function parameters
            args = copy.deepcopy(max_node.args)
            
            # Create new MIN function node
            min_node = sqlglot.expressions.Min(**args)
            
            # Replace node
            max_node.replace(min_node)
            return min_node
        except Exception as e:
            pass  # Silently handle exceptions

    def _mutate_count_to_count_distinct(self, count_node):
        """Mutate COUNT function to COUNT(DISTINCT) function"""
        try:
            if hasattr(count_node, 'this') and count_node.this and not self._is_count_distinct(count_node):
                # Save original expression
                original_expr = copy.deepcopy(count_node.this)
                
                # Create Distinct node and wrap original expression
                distinct_node = sqlglot.expressions.Distinct(expressions=[original_expr])
                
                # Create new Count node containing Distinct node
                new_count_node = sqlglot.expressions.Count(this=distinct_node, big_int=count_node.args.get('big_int', True))
                
                # Use sqlglot's replace method to replace the entire node
                count_node.replace(new_count_node)
                return new_count_node
        except Exception as e:
            pass  # Silently handle exceptions

    def _mutate_count_distinct_to_count(self, count_node):
        """Mutate COUNT(DISTINCT) function to COUNT function"""
        try:
            if hasattr(count_node, 'this') and count_node.this and self._is_count_distinct(count_node):
                # Get expression within Distinct node
                if hasattr(count_node.this, 'expressions') and count_node.this.expressions:
                    # Get original expression
                    original_expr = count_node.this.expressions[0]
                    
                    # Create new Count node without Distinct
                    new_count_node = sqlglot.expressions.Count(this=original_expr, big_int=count_node.args.get('big_int', True))
                    
                    # Use sqlglot's replace method to replace the entire node
                    count_node.replace(new_count_node)
                    return new_count_node
        except Exception as e:
            pass  # Silently handle exceptions
    
    def _mutate_add_positive_random(self, func_node):
        """Implement func-->func + random(random>0) mutation for numeric type parameters"""
        try:
            from sqlglot.expressions import Add, Literal
            import random
            
            # Generate a positive random number (integer between 1 and 10)
            random_value = random.randint(1, 10)

            # Create random number literal
            random_literal = Literal(this=str(random_value), is_string=False)
            
            # Create addition expression: func(x) + random_value
            add_expression = Add(this=func_node.copy(), expression=random_literal)
            
            # Replace original function node
            func_node.replace(add_expression)
            return add_expression
        except Exception as e:
            return False


    def _mutate_variance_to_stddev(self, variance_node):
        """Mutate VARIANCE/VarPop/VarSamp function to STDDEV function"""
        try:
            # Get VARIANCE function parameters
            args = copy.deepcopy(variance_node.args)
            # Use Anonymous node to directly create STDDEV function
            from sqlglot.expressions import Anonymous
            if 'this' in args:
                column_arg = args['this']
                stddev_node = Anonymous(this='STDDEV', expressions=[column_arg])
            else:
                # Handle case where 'this' parameter might not exist
                stddev_node = Anonymous(this='STDDEV')
            
            variance_node.replace(stddev_node)
            return stddev_node
        except Exception as e:
            pass  # Silently handle exceptions
    
    def _mutate_stddev_to_variance(self, stddev_node):
        """Mutate STDDEV_SAMP function to VAR_SAMP function
        This method uses Anonymous node to directly create VAR_SAMP function to ensure correct VAR_SAMP function calls are generated across all database dialects."""
        try:
            # Get STDDEV_SAMP function parameters
            args = copy.deepcopy(stddev_node.args)
            
            # Extract actual column parameter (this attribute) from arguments
            if 'this' in args:
                column_arg = args['this']
                
                # Use Anonymous node to directly create VAR_SAMP function
                # This method ensures VAR_SAMP function name is used directly in the generated SQL statement
                from sqlglot.expressions import Anonymous
                var_samp_node = Anonymous(this='VAR_SAMP', expressions=[column_arg])
                
                # Replace node
                stddev_node.replace(var_samp_node)
                return var_samp_node
        except Exception as e:
            pass  # Silently handle exception
            
    def _mutate_regular_column_constant(self, column_node, constant):
        """
        Perform constant mutation on regular column or scalar function (column/function + constant)
        
        Parameters:
            column_node: Column node or scalar function node
            constant: Constant value to add
        """
        try:
            from sqlglot.expressions import Add, Literal
            
            # Create constant literal, ensuring it's numeric type
            # Generate random integer between 1-10 as constant value
            import random
            random_constant = random.randint(1, 10)
            constant_literal = Literal(this=str(random_constant), is_string=False)
            # Import multiplication operator
            from sqlglot.expressions import Mul
            
            # Check if it's a trigonometric function, if so, only perform addition mutation
            import random
            operation_type = 'add'  # Use addition by default
            
            # Check if it's a trigonometric function
            if not (hasattr(column_node, 'name') and isinstance(column_node.name, str) and 
                    column_node.name.upper() in ['SIN', 'COS', 'TAN', 'COT', 'SEC', 'CSC', 'ASIN', 'ACOS', 'ATAN', 'ATAN2']):
                # If not a trigonometric function, randomly select addition or multiplication operation
                operation_type = random.choice(['add', 'mul'])

            
            if operation_type == 'add':
                # Create an Add expression: column_node + constant
                expr = Add(
                    this=column_node.copy(),  # Copy original column or function to avoid direct modification
                    expression=constant_literal  # Constant
                )
            else:  # operation_type == 'mul'
                # Create a Mul expression: column_node * constant
                expr = Mul(
                    this=column_node.copy(),  # Copy original column or function to avoid direct modification
                    expression=constant_literal  # Constant value
                )
            
            # Use sqlglot's replace method to replace node
            column_node.replace(expr)
            return expr
        except Exception as e:
            pass
    
    def _mutate_monotonic_multiplication(self, func_node, multiplier):
        """Perform multiplication operation on function parameters while maintaining function monotonicity
        
        Parameters:
            func_node: The function node to mutate
            multiplier: The constant to multiply by
        """
        try:
            from sqlglot.expressions import Mul, Literal, Sum, Distinct
            
            # Check if it's a SUM function with DISTINCT
            is_sum_distinct = False
            distinct_expressions = None
            
            # Get original function parameters
            original_arg = None
            if hasattr(func_node, 'this') and func_node.this:
                # Check if DISTINCT is included
                if hasattr(func_node.this, '__class__') and func_node.this.__class__.__name__ == 'Distinct':
                    is_sum_distinct = True
                    if hasattr(func_node.this, 'expressions') and func_node.this.expressions:
                        distinct_expressions = func_node.this.expressions
                        original_arg = distinct_expressions[0]
                else:
                    original_arg = func_node.this
            elif hasattr(func_node, 'expressions') and func_node.expressions:
                original_arg = func_node.expressions[0]
            
            if original_arg:
                # Create multiplication expression: original parameter * constant
                multiplier=f"{multiplier}"
                constant = Literal(this=multiplier,is_string = False)
                multiplied_arg = Mul(this=original_arg, expression=constant)
                
                # Adopt different processing methods based on whether DISTINCT is present
                if is_sum_distinct and func_node.__class__.__name__ == 'Sum':
                    # For SUM(DISTINCT), create new Distinct and Sum nodes
                    new_distinct = Distinct(expressions=[multiplied_arg])
                    # For cases that don't support direct this attribute setting, create a new Sum node
                    new_sum = Sum(this=new_distinct)
                    # Replace the entire node
                    func_node.replace(new_sum)
                    return new_sum
                else:
                    # Update function parameters
                    if hasattr(func_node, 'this'):
                        try:
                            func_node.this = multiplied_arg
                        except AttributeError:
                            # If direct this attribute setting is not possible, create and replace with new node
                            if func_node.__class__.__name__ == 'Sum':
                                new_sum = Sum(this=multiplied_arg)
                                func_node.replace(new_sum)
                    elif hasattr(func_node, 'expressions'):
                        func_node.expressions[0] = multiplied_arg
                    
                
                    return new_sum
        except Exception as e:
            pass  # Silently handle exceptions

    def get_mutated_ast(self):
        """Get the mutated AST"""
        if not self.mutated:
            return self.ast
        return self.ast

    def get_aggregate_count(self):
        """Get the count of identified aggregate functions"""
        return len(self.aggregate_nodes)
                
    def compare_results(self, original_result, mutated_result, node_info, original_sql, root_node=None):
        """Compare original and mutated results
        
        Parameters:
            original_result: Original query result
            mutated_result: Mutated query result
            node_info: Node information containing function name, path, etc.
            original_sql: Original SQL query statement
            root_node: Root node of the node in node_info
        
        Returns:
            dict: Dictionary containing comparison results
        """
        comparison_result = {
            'are_results_same': False,
            'has_same_structure': False,
            'has_same_row_count': False,
            'func_name': node_info['func_name'],
            'node_path_list': node_info['node_path_list'],
            'node_path_list_reversed': node_info['node_path_list_reversed'],
            'original_sql': original_sql,
            'query_type': 'Regular SELECT query',  # Default to regular query
            'branch_type': 'Unknown branch',  # New field: branch where aggregate function is located
            'original_result': original_result,  # Store original query result
            'mutated_result': mutated_result,    # Store mutated query result
            'is_expected': None  # New parameter: mark whether the mutation result is as expected
        }
        self.node_info_stack=[]
        # Push node_info onto the stack
        self.node_info_stack.append(node_info)
        branch_type=self.get_branch_type(node_info,comparison_result)
        # Return comparison result
        if self.node_info_stack:
            node_info = self.node_info_stack[0]
        reversed_list=node_info['node_path_list_reversed']
        self._compare_by_branch(comparison_result, branch_type, node_info, reversed_list, original_sql, root_node)
        return comparison_result

    def get_branch_type(self,node_info,comparison_result):
        # Determine query type
        node_path_list = node_info['node_path_list']
        reversed_list = node_info['node_path_list_reversed']
        # Use while loop instead of for loop, stop when node is empty
        i = 1
        while i < len(reversed_list):
            path_item = reversed_list[i]
            if path_item in ['Where', 'Having']:
                branch_type = 'where_affected'
                break
            if path_item == 'Select':
                pass
                
            if path_item == 'Alias':
                if node_info['column_alias'] != None:
                    comparison_result['query_type'] = 'SELECT query with alias'
                    branch_type = 'column_value_affected'
            if path_item == 'Subquery':
                if i+1 == len(reversed_list):
                    break
                if reversed_list[i+1] == 'From':
                    # Find where the current node_info is referenced in the upper-level query
                # Update node_info's node, column_alias, reversed_list
                    if node_info['node']:
                        # Add print statement to check initial value
                        current_node=node_info['node']
                        parent_node = None
                        
                        # Traverse upward until Select node is found or root node is reached
                        traversal_count = 0
                        num = 0
                        while current_node:
                            if hasattr(current_node, 'parent') and current_node.parent:
                                parent_node = current_node.parent
                                # Check if parent node is of Select type
                                parent_node_type = parent_node.__class__.__name__
                            
                                if parent_node_type == 'Select':
                                    num += 1
                                    if num ==2:
                                        break
                                    else:
                                        current_node = parent_node
                                        traversal_count += 1
                                    # Continue upward traversal
                                else:
                                    current_node = parent_node
                                    traversal_count += 1
                            else:
                                # No parent node, reached root node
                                parent_node = None
                                break
                        if parent_node:
                            # Find the reference to the target column in the upper-level query
                            if parent_node and hasattr(parent_node, 'expressions'):
                                # Iterate through all expressions in the parent query to find references to the target column
                                for expr in parent_node.expressions:
                                    expr_type = expr.__class__.__name__
                                    if expr_type == 'Alias' and hasattr(expr,'this'):
                                        expr=expr.this    
                                    # Check if the expression references nodes or columns in the current node_info
                                # Find target column only through Column type nodes: table name is subquery alias, column name is column alias in subquery
                                    found_reference = False
                                    ref_type = "Not found"
                                    
                                    # Method: Find Column type node where table name is subquery alias and column name is column alias in subquery
                                    if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' and 
                                          hasattr(expr, 'table') and expr.table and 
                                          hasattr(expr, 'this') and expr.this and 
                                          hasattr(expr.this, 'name') and 
                                          'subquery_alias' in node_info and node_info['subquery_alias'] and 
                                          'column_alias' in node_info and node_info['column_alias'] and 
                                          expr.table == node_info['subquery_alias'] and 
                                          expr.this.name == node_info['column_alias']):
                                        # Find Column type node where table name is subquery alias and column name is column alias in subquery
                                        found_reference = True
                                        ref_type = "Subquery alias + column alias match"
                                    elif (hasattr(expr, '__class__') and expr.__class__.__name__ == 'FunctionCallNode' and 
                                          hasattr(expr, 'children') and expr.children):
                                        # Process function type expressions, check if their parameters meet requirements
                                    # Iterate through all parameters of the function
                                        for param in expr.children:
                                            param_type = param.__class__.__name__
                                            if param_type == 'Alias' and hasattr(param, 'this'):
                                                param = param.this
                                            # Check if parameter is of Column type and meets requirements
                                            if (hasattr(param, '__class__') and param.__class__.__name__ == 'Column' and 
                                                  hasattr(param, 'table') and param.table and 
                                                  hasattr(param, 'this') and param.this and 
                                                  hasattr(param.this, 'name') and 
                                                  'subquery_alias' in node_info and node_info['subquery_alias'] and 
                                                  'column_alias' in node_info and node_info['column_alias'] and 
                                                  param.table == node_info['subquery_alias'] and 
                                                  param.this.name == node_info['column_alias']):
                                                # Found function parameter that meets requirements
                                                found_reference = True
                                                ref_type = f"Function parameter match (function name: {getattr(expr, 'function', {}).get('name', 'Unknown')})"
                                                break
                                        
                                    if found_reference:
                                        # Found where the current node_info is referenced in the upper-level query
                                        # Create new node_info to store new information instead of directly updating the original node_info
                                        new_node_info = node_info.copy()
                                        
                                        # Update new_node_info information
                                        if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                            new_node_info['column_alias'] = expr.parent.alias
                                        # Update subquery_alias to the alias of the parent query
                                        subquery_alias = None
                                        try:
                                            # Search upward for the alias of the upper-level query
                                            current_expr_node = expr
                                            while current_expr_node:
                                                if hasattr(current_expr_node, '__class__') and current_expr_node.__class__.__name__ == 'Subquery':
                                                    if hasattr(current_expr_node, 'alias') and current_expr_node.alias:
                                                        subquery_alias = current_expr_node.alias
                                                        break
                                                if hasattr(current_expr_node, 'parent'):
                                                    current_expr_node = current_expr_node.parent
                                                else:
                                                    subquery_alias = None
                                                    break
                                        except Exception as e:
                                            subquery_alias = None
                                        new_node_info['subquery_alias'] = subquery_alias
                                        # Update new_node_info's node to the found reference node
                                        new_node_info['node'] = expr
                                        # Update path information to include the complete path of the parent level
                                        new_path_list = self._get_node_path_list(expr)
                                        new_node_info['node_path_list'] = new_path_list
                                        new_node_info['node_path_list_reversed'] = new_path_list[::-1]
                                        # Push new_node_info to stack, pop bottom element
                                        self.node_info_stack.append(new_node_info)
                                        new_reversed_list = new_node_info['node_path_list_reversed']
                                        branch_type = self.get_branch_type(new_node_info, comparison_result)
                            if found_reference:
                                self.node_info_stack.pop(0)
                                            
                            if not found_reference and 'where' in parent_node.args:
                                where_expr = parent_node.args['where']
                                if where_expr:
                                    # Iterate through all expressions in the Where clause
                                    for expr in where_expr.walk():
                                        expr_type = expr.__class__.__name__
                                        # Check if expression references nodes or columns in current node_info
                                        found_reference = False
                                        ref_type = "Not found"
                                        
                                        # Method: Find Column type node where table name is subquery alias and column name is column alias in subquery
                                        if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' and 
                                              hasattr(expr, 'table') and expr.table and 
                                              hasattr(expr, 'this') and expr.this and 
                                              hasattr(expr.this, 'name') and 
                                              'subquery_alias' in node_info and node_info['subquery_alias'] and 
                                              'column_alias' in node_info and node_info['column_alias'] and 
                                              expr.table == node_info['subquery_alias'] and 
                                              expr.this.name == node_info['column_alias']):
                                            # Find Column type node where table name is subquery alias and column name is column alias in subquery
                                            found_reference = True
                                            ref_type = "Where clause subquery alias + column alias match"
                                            
                                        if found_reference:
                                            # Found where the current node_info is referenced in the upper-level query
                                            # Create new node_info to store new information instead of directly updating the original node_info
                                            new_node_info = node_info.copy()
                                            
                                            # Update new_node_info information
                                            if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                                new_node_info['column_alias'] = expr.parent.alias
                                            # Update new_node_info's node to the found reference node
                                            new_node_info['node'] = expr
                                            # Update path information to include the complete path of the parent level
                                            new_path_list = self._get_node_path_list(expr)
                                            new_node_info['node_path_list'] = new_path_list
                                            new_node_info['node_path_list_reversed'] = new_path_list[::-1]
                                            new_reversed_list = new_node_info['node_path_list_reversed']
                                           
                                            branch_type = self.get_branch_type(new_node_info, comparison_result)
                                            # Use the new node_info
                                            node_info = new_node_info
                                            break
                                    if found_reference:
                                        break

                                # If reference not found in Where clause, look in Having clause
                            if not found_reference and 'having' in parent_node.args:
                                    having_expr = parent_node.args['having']
                                    if having_expr:
                                        # Iterate through all expressions in the Having clause
                                        for expr in having_expr.walk():
                                            expr_type = expr.__class__.__name__
                                             
                                            # Check if the expression references nodes or columns in the current node_info
                                            found_reference = False
                                            ref_type = "Not found"
                                             
                                            # Method: Find Column type node where table name is subquery alias and column name is column alias in subquery
                                            if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' and 
                                                  hasattr(expr, 'table') and expr.table and 
                                                  hasattr(expr, 'this') and expr.this and 
                                                  hasattr(expr.this, 'name') and 
                                                  'subquery_alias' in node_info and node_info['subquery_alias'] and 
                                                  'column_alias' in node_info and node_info['column_alias'] and 
                                                  expr.table == node_info['subquery_alias'] and 
                                                  expr.this.name == node_info['column_alias']):
                                                # Find Column type node where table name is subquery alias and column name is column alias in subquery
                                                found_reference = True
                                                ref_type = "Having clause subquery alias + column alias match"
                                                 
                                            if found_reference:
                                                # Found where the current node_info is referenced in the upper-level query
                                                # Update node_info information
                                                if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                                    node_info['column_alias'] = expr.parent.alias
                                                # Update node_info's node to the found reference node
                                                node_info['node'] = expr
                                                # Update path information to include the complete path of the parent level
                                                new_path_list = self._get_node_path_list(expr)
                                                node_info['node_path_list'] = new_path_list
                                                node_info['node_path_list_reversed'] = new_path_list[::-1]
                                                new_reversed_list=node_info['node_path_list_reversed']
                                                branch_type=self.get_branch_type(node_info,comparison_result)
                                                break
                                        if found_reference:
                                            break
                            if not found_reference:
                                return 'same'
                elif reversed_list[i+1] == 'Alias':
                    # Create new node_info object instead of directly updating the original node_info
                    if node_info['node']:
                        # Deep copy original node_info
                        node_info_new = {key: value for key, value in node_info.items()}
                        current_node = node_info['node']
                        select_node_found = False
                        # Traverse upward until finding a Select node or reaching the root node
                        while current_node:
                            if hasattr(current_node, 'parent') and current_node.parent:
                                parent_node = current_node.parent
                                if hasattr(parent_node, '__class__') and parent_node.__class__.__name__ == 'Subquery':
                                    node_info_new['node'] = parent_node
                                    select_node_found = True
                                    break
                                current_node = parent_node
                            else:
                                break
                        if select_node_found:
                            # Update path information
                            new_path_list = self._get_node_path_list(node_info_new['node'])
                            node_info_new['node_path_list'] = new_path_list
                            node_info_new['node_path_list_reversed'] = new_path_list[::-1]
                            new_reversed_list = node_info_new['node_path_list_reversed']
                            # Get the alias of the first Alias node by traversing upward from the current node
                            current_node = node_info_new['node']
                            column_alias = None
                            while current_node and hasattr(current_node, 'parent'):
                                parent_node = current_node.parent
                                if hasattr(parent_node, '__class__') and parent_node.__class__.__name__ == 'Alias' and hasattr(parent_node, 'alias'):
                                    column_alias = parent_node.alias
                                    break
                                current_node = parent_node
                            if column_alias:
                                node_info_new['column_alias'] = column_alias
                            # Replace original node_info with newly created node_info
                            self.node_info_stack.append(node_info_new)
                            #self.node_info_stack.pop(0)
                            branch_type=self.get_branch_type(node_info_new, comparison_result)       
                            break
                elif reversed_list[i+1] == 'In':
                    branch_type = None
                    break            
            if path_item == 'CTE':
                final_success=False
                if not node_info['outer_alias']:
                    return 'same'
                # Find Column type node in the main query where table name is outer_alias and column is column_alias
                if node_info['node'] and 'outer_alias' in node_info and node_info['outer_alias'] and 'column_alias' in node_info and node_info['column_alias']:
                    current_node=node_info['node']
                    root_node = None
                    
                    # Traverse upward to find the root node
                    while current_node:
                        if hasattr(current_node, 'parent') and current_node.parent:
                            current_node = current_node.parent
                        else:
                            root_node = current_node
                            break
                    if root_node:
                        # Traverse downward from root node one by one, skip With type nodes and their children, record all Select type nodes as main query nodes
                        main_queries = []
                        current_nodes = [root_node]
                        for node in root_node.walk():
                            # Check if node is of With type, if so skip
                            if hasattr(node, '__class__') and node.__class__.__name__ == 'With':
                                continue
                            # Check if node is in the subtree of a With node, if so skip
                            current_parent = node
                            is_with_descendant = False
                            while hasattr(current_parent, 'parent') and current_parent.parent:
                                current_parent = current_parent.parent
                                if hasattr(current_parent, '__class__') and current_parent.__class__.__name__ == 'With':
                                    is_with_descendant = True
                                    break
                            if is_with_descendant:
                                continue
                            # If it's not a With node or its children, and is of Select type, add to main query list
                            if hasattr(node, '__class__') and node.__class__.__name__ == 'Select':
                                main_queries.append(node)
                        # Find references in all main queries
                        for main_query in main_queries:
                            if main_query and hasattr(main_query, 'expressions'):
                                # Iterate through all expressions of main query to find references to target column
                                for expr in main_query.expressions:
                                    # Check if it's an Alias node, get actual expression
                                    if hasattr(expr, '__class__') and expr.__class__.__name__ == 'Alias' and hasattr(expr, 'this'):
                                        expr = expr.this

                                    # Check if expression is of Column type and table name and column name match
                                    found_reference = False
                                    if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' ):                                    
                                        if(hasattr(expr, 'table')):
                                            if(hasattr(expr, 'this')):
                                                if(isinstance(node_info['outer_alias'], str) and expr.table == str(node_info['outer_alias']) or isinstance(node_info['outer_alias'], list) and expr.table in [str(alias) for alias in node_info['outer_alias']]):
                                                    if(expr.this.name == node_info['column_alias']):
                                                        final_success=True
                                                        found_reference = True
                                                        ref_type = "CTE outer_alias+column_alias match"
                                    # Check if expression is of Anonymous type, handle function calls like DATE_FORMAT(feb94.c5, '%Y/%m/%d')
                                    elif (hasattr(expr, '__class__') and expr.__class__.__name__ in ['Anonymous'] ):
                                        # Extract table name and column name information from function call
                                        if hasattr(expr, 'expressions') and len(expr.expressions) > 0:
                                            for i,first_arg in enumerate(expr.expressions):
                                                # Check if first parameter is of Column type and matches CTE reference
                                                if (hasattr(first_arg, '__class__') and first_arg.__class__.__name__ == 'Column' and
                                                    hasattr(first_arg, 'table') and hasattr(first_arg, 'this') and
                                                    (isinstance(node_info['outer_alias'], str) and first_arg.table == str(node_info['outer_alias']) or isinstance(node_info['outer_alias'], list) and first_arg.table in [str(alias) for alias in node_info['outer_alias']]) and
                                                    first_arg.this.name == node_info['column_alias']):
                                                    
                                                        final_success=True
                                                        found_reference = True
                                                        ref_type = "CTE Anonymous function call reference match"
                                    # Check if expression is of Abs type, handle function calls like abs(column_name)
                                    elif hasattr(expr, '__class__') and expr.__class__.__name__ in ['Abs', 'Log', 'Lower', 'Upper','Exp','Length','Substring']:
                                        
                                        # Process function type expressions, check if their parameters meet requirements
                                        # Iterate through all parameters of the function
                                        if hasattr(expr,'this'):
                                            param=expr.this
                                            param_type = param.__class__.__name__
                                            if param_type == 'Alias' and hasattr(param, 'this'):
                                                param = param.this
                                            # Check if parameter is of Column type and meets requirements
                                            if (hasattr(param, '__class__') and param.__class__.__name__ == 'Column' and 
                                                  hasattr(param, 'table') and param.table and 
                                                  hasattr(param, 'this') and param.this and 
                                                  hasattr(param.this, 'name') and 
                                                  'outer_alias' in node_info and node_info['outer_alias'] and 
                                                  'column_alias' in node_info and node_info['column_alias'] and 
                                                  (isinstance(node_info['outer_alias'], str) and param.table == str(node_info['outer_alias']) or isinstance(node_info['outer_alias'], list) and param.table in [str(alias) for alias in node_info['outer_alias']]) and
                                                  param.this.name == node_info['column_alias']):
                                                # Found function parameter that meets requirements
                                                final_success=True
                                                found_reference = True
                                                ref_type = f"CTE function parameter match (function name: {getattr(expr, 'function', {}).get('name', 'Unknown')})"
                                    elif hasattr(expr, '__class__') and expr.__class__.__name__ == 'Round':
                                        # Check if Round function parameter is of Column type
                                        if hasattr(expr, 'args') and len(expr.args) > 0:
                                                param=expr.this
                                                if (hasattr(param, '__class__') and param.__class__.__name__ == 'Column' and 
                                                  hasattr(param, 'table') and param.table and 
                                                  hasattr(param, 'this') and param.this and 
                                                  hasattr(param.this, 'name') and 
                                                  'outer_alias' in node_info and node_info['outer_alias'] and 
                                                  'column_alias' in node_info and node_info['column_alias'] and 
                                                  (isinstance(node_info['outer_alias'], str) and param.table == str(node_info['outer_alias']) or isinstance(node_info['outer_alias'], list) and param.table in [str(alias) for alias in node_info['outer_alias']]) and
                                                  param.this.name == node_info['column_alias']):
                                                    final_success=True
                                                    found_reference = True
                                                    ref_type = f"CTE function parameter match (function name: {getattr(expr, 'function', {}).get('name', 'Unknown')})"  
                                    if found_reference:
                                        # Create a new node_info object instead of directly updating the original
                                        node_info_new = {key: value for key, value in node_info.items()}
                                        # Update node_info_new information
                                        if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                            node_info_new['column_alias'] = expr.parent.alias
                                        # Update node_info_new's node to the found reference node
                                        node_info_new['node'] = expr
                                        # Update path information to include the complete path with parent nodes
                                        new_path_list = self._get_node_path_list(expr)
                                        node_info_new['node_path_list'] = new_path_list
                                        node_info_new['node_path_list_reversed'] = new_path_list[::-1]
                                        self.node_info_stack.append(node_info_new)
                                        # Recursively call get_branch_type to process the updated node_info_new
                                        branch_type=self.get_branch_type(node_info_new, comparison_result)
                                if not final_success and 'where' in main_query.args:
                                    where_expr = main_query.args['where']
                                    if where_expr:
                                        # Iterate through all expressions in the Where clause
                                        for expr in where_expr.walk():
                                            expr_type = expr.__class__.__name__
                                            # Check if the expression references nodes or columns in current node_info
                                            found_reference = False
                                            ref_type = "Not found"
                                              
                                            # Method: Find Column type nodes where table name is subquery alias and column name is column alias in the subquery
                                            if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' and 
                                                  hasattr(expr, 'table') and expr.table and 
                                                  hasattr(expr, 'this') and expr.this and 
                                                  hasattr(expr.this, 'name') and 
                                                  'outer_alias' in node_info and node_info['outer_alias'] and 
                                                  'column_alias' in node_info and node_info['column_alias'] and 
                                                  (expr.table == str(node_info['outer_alias']) if isinstance(node_info['outer_alias'], str) else expr.table in [str(alias) for alias in node_info['outer_alias']]) and 
                                                  expr.this.name == node_info['column_alias']):
                                                # Find Column type nodes where table name is CTE outer alias and column name is column alias in CTE
                                                final_success=True
                                                found_reference = True
                                                ref_type = "CTE Where clause outer_alias+column_alias match"
                                                 
                                            if found_reference:
                                                # Found reference to current node_info in the parent query
                                                # Update node_info information
                                                if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                                    node_info['column_alias'] = expr.parent.alias
                                                # Update node_info's node to the found reference node
                                                node_info['node'] = expr
                                                # Update path information to include the complete path with parent nodes
                                                new_path_list = self._get_node_path_list(expr)
                                                node_info['node_path_list'] = new_path_list
                                                node_info['node_path_list_reversed'] = new_path_list[::-1]
                                                branch_type=self.get_branch_type(node_info,comparison_result)
                                                break

                                 
                                # If reference not found in Where clause, look in Having clause
                                if not final_success and 'having' in main_query.args:
                                    having_expr = main_query.args['having']
                                    if having_expr:
                                        # Iterate through all expressions in the Having clause
                                        for expr in having_expr.walk():
                                            expr_type = expr.__class__.__name__
                                                  
                                            # Check if the expression references nodes or columns in current node_info
                                            found_reference = False
                                            ref_type = "Not found"
                                                  
                                            # Method: Find Column type nodes where table name is subquery alias and column name is column alias in the subquery
                                            if (hasattr(expr, '__class__') and expr.__class__.__name__ == 'Column' and 
                                                      hasattr(expr, 'table') and expr.table and 
                                                      hasattr(expr, 'this') and expr.this and 
                                                      hasattr(expr.this, 'name') and 
                                                      'outer_alias' in node_info and node_info['outer_alias'] and 
                                                      'column_alias' in node_info and node_info['column_alias'] and 
                                                      (isinstance(node_info['outer_alias'], str) and expr.table == str(node_info['outer_alias']) or isinstance(node_info['outer_alias'], list) and expr.table in [str(alias) for alias in node_info['outer_alias']]) and 
                                                      expr.this.name == node_info['column_alias']):
                                                    # Find Column type nodes where table name is CTE outer alias and column name is column alias in CTE
                                                    found_reference = True
                                                    ref_type = "CTE Having clause outer_alias+column_alias match"
                                                 
                                            if found_reference:
                                                    # Found reference to current node_info in the parent query
                                                    # Update node_info information
                                                    if hasattr(expr.parent, 'alias') and expr.parent.alias:
                                                        node_info['column_alias'] = expr.parent.alias
                                                    # Update node_info's node to the found reference node
                                                    node_info['node'] = expr
                                                    # Update path information to include the complete path with parent nodes
                                                    new_path_list = self._get_node_path_list(expr)
                                                    node_info['node_path_list'] = new_path_list
                                                    node_info['node_path_list_reversed'] = new_path_list[::-1]
                                                    final_success=True
                                                    branch_type=self.get_branch_type(node_info,comparison_result)
                                                    break
                                        if found_reference:
                                            break
                        self.node_info_stack.pop(0)
                        if not final_success:
                            branch_type='same'
                            return branch_type
                    break        
            i += 1
        return branch_type
    
    def _get_parent_type(self, node):
        """Get the parent node type of the node"""
        if hasattr(node, 'parent') and node.parent:
            return node.parent.__class__.__name__
        return "Root"
    
    def _get_node_path(self, node):
        """Get the path information of the node, representing the node's position in the AST as a string, including left and right operand information for set operations"""
        path_list = self._get_node_path_list(node)
        return " -> ".join(path_list)
        
    def _get_node_path_list(self, node):
        """Get the node path information list, from root node to current node"""
        path = []
        current = node
        
        # Traverse upward until reaching the root node
        while current:
            node_type = current.__class__.__name__
            
            # Check if current node is part of a set operation
            operation_side_info = self._get_operation_side_info(current)
            if operation_side_info:
                node_with_side = f"{node_type} [{operation_side_info}]"
                path.append(node_with_side)
            else:
                path.append(node_type)
                
            if hasattr(current, 'parent'):
                current = current.parent
            else:
                break
        
        # Reverse path, starting from root node
        path.reverse()
        return path
          
    def _compare_by_branch(self, comparison_result, branch_type, node_info, reversed_list, original_sql, root_node=None):
        """Perform specific comparison logic based on the branch where the aggregate function is located"""
        
        if branch_type in ['where_affected', 'having_affected']:
            # WHERE or having_affected: affects the size of the query result set
            # Set default expected result to True
            comparison_result['is_expected'] = None
            
            # Determine comparison operator
            comparison_op = None
            for node_type in reversed_list:
                if any(op in node_type for op in ['GT', 'GTE', 'LT', 'LTE', 'EQ', 'NEQ']):
                    comparison_op = node_type
                    break
            if comparison_op in ['GT','GTE']:
                # Greater than or greater than/equal to: Expected result is True (has results)
                flag=1
            elif comparison_op in ['LT','LTE']:
                # Less than or less than/equal to: Expected result is False (no results)
                flag=0
            else:
                # Equal to or not equal to: Expected result is False (no results)
                flag=0.5
            # If original and mutated results are provided, perform result set comparison
            if 'original_result' in comparison_result and 'mutated_result' in comparison_result:
                original_data = comparison_result['original_result']
                mutated_data = comparison_result['mutated_result']
                # Extract row data from original and mutated results
                original_rows = self.extract_rows(original_data)
                mutated_rows = self.extract_rows(mutated_data)
                
                # Calculate row counts
                original_row_count = len(original_rows)
                mutated_row_count = len(mutated_rows)
                
    
                
                if flag != 0.5:
                    final_direction = node_info['direction']^flag^1
                else:
                    comparison_result['is_expected']=None
                    return 
                # Check if the column appears in both WHERE/HAVING and SELECT clauses
                if True:
                    # Regular comparison logic: Compare both row count and element presence
                    # Determine result relationship based on final_direction: 1 for superset, 0 for subset
                    if final_direction == 1:
        
                        # Check if all elements in the small set (original result) exist in the large set (mutated result)
                        if mutated_row_count >= original_row_count:
                            # Iterate through elements in the small set, remove them from the large set when found, until the small set is empty
                            # Create temporary row lists
                            temp_original_rows = list(original_rows)
                            temp_mutated_rows = list(mutated_rows)
                            all_elements_present = True
                            
                            # Iterate through elements in the small set, remove matching elements from the large set
                            for row in temp_original_rows[:]:
                                match_found = False
                                for i, mutated_row in enumerate(temp_mutated_rows):
                                    if self.rows_match_excluding_window_cols(row, mutated_row, original_sql):
                                        temp_original_rows.remove(row)
                                        temp_mutated_rows.pop(i)
                                        match_found = True
                                        break
                                if not match_found:
                                    all_elements_present = False
                                    break
                            
                            # Only consider all elements as present when the smaller set is empty
                            all_elements_present = all_elements_present and len(temp_original_rows) == 0
                            
                            if all_elements_present:
                                comparison_result['is_expected'] = True

                            else:
                                comparison_result['is_expected'] = False

                        else:
                            comparison_result['is_expected'] = False

                    else:
                        
                        # Check if all elements in the small set (mutated result) exist in the large set (original result)
                        if mutated_row_count <= original_row_count:
                            # Iterate through elements in the small set, remove them from the large set when found, until the small set is empty

                            
                            # Create temporary row lists
                            temp_original_rows = list(original_rows)
                            temp_mutated_rows = list(mutated_rows)
                            all_elements_present = True
                            
                            # Iterate through elements in the small set, remove matching elements from the large set
                            for row in temp_mutated_rows[:]:
                                match_found = False
                                for i, original_row in enumerate(temp_original_rows):
                                    if self.rows_match_excluding_window_cols(row, original_row, original_sql):
                                        temp_mutated_rows.remove(row)
                                        temp_original_rows.pop(i)
                                        match_found = True
                                        break
                                if not match_found:
                                    all_elements_present = False
                                    break
                            
                            # Consider all elements present only when the small set is empty
                            all_elements_present = all_elements_present and len(temp_mutated_rows) == 0
                            
                            if all_elements_present:
                                comparison_result['is_expected'] = True

                            else:
                                comparison_result['is_expected'] = False

                        else:
                            comparison_result['is_expected'] = False

                          
        elif branch_type == 'column_value_affected':
            # column_value_affected: Affects the value size of specific columns in the current SQL result
            
            # Determine the specific column where the aggregate function is located
            # Modified to support multiple mutated columns
            mutated_columns_index = []  # Store all found mutated column information
            # Locate mutated columns by comparing with nodes in the node_info_stack
            
            # Check if root_node is a set type (Union, Intersect, Except), and if so, get all main query parts
            target_nodes = []
            if root_node:
                # Check if root_node is a set operation type
                root_node_type = root_node.__class__.__name__
                if root_node_type in ['Union', 'Intersect', 'Except']:
                    # It's a set type, get all main query parts (left and right operands)
                   
                    while root_node.__class__.__name__ in ['Union', 'Intersect', 'Except']:
                        if hasattr(root_node,'left') and root_node.left:
                            if root_node.left.__class__.__name__ == 'Select':
                                target_nodes.append(root_node.left)
                            elif root_node.left.__class__.__name__ == 'Subquery':
                                target_nodes.append(root_node.left.this)
                        if hasattr(root_node, 'right') and root_node.right:
                            if root_node.right.__class__.__name__ == 'Select':
                                target_nodes.append(root_node.right)
                            elif root_node.right.__class__.__name__ == 'Subquery':
                                target_nodes.append(root_node.right.this)
                        root_node=root_node.left
                else:
                    # Not a set type, use root_node directly
                    target_nodes.append(root_node)
            
            # Iterate through all target nodes (could be a single root node or multiple subqueries from set operations)
            for target in target_nodes:
                if target and hasattr(target, 'expressions'):
                    # Iterate through target's expressions
                    for i, expr in enumerate(target.expressions):
                        # Check if the current expression matches nodes in node_info_stack
                        if expr.__class__.__name__ == 'Alias':
                            expr = expr.this

                        for stack_node_info in self.node_info_stack:
                            target_node = stack_node_info.get('node')
                            if not target_node:
                                continue
                            # Check if the expression directly equals the target node
                            if expr == target_node:
                                # Get column name (if there's an alias)
                                col_name = None
                                if hasattr(expr, 'name'):
                                    col_name = expr.name
                                elif hasattr(expr, 'alias') and expr.alias:
                                    col_name = expr.alias
                                elif hasattr(expr, '__class__'):
                                    col_name = f"{expr.__class__.__name__}_{i}"
                                # Add the found mutated column information to the list
                                mutated_columns_index.append({
                                    'index': i,
                                    'name': col_name,
                                    'node': target_node,
                                    'stack_node_info': stack_node_info
                                })
                            
                            # For function types, check if its argument nodes are the same as the target node
                            if hasattr(expr, '__class__') and 'Abs' in expr.__class__.__name__ and hasattr(expr, 'args'):

                                for arg in expr.args:
                                    if arg == target_node:
                                        # Get column name (if there's an alias)
                                        col_name = None
                                        if hasattr(expr, 'name'):
                                            col_name = expr.name
                                        elif hasattr(expr, 'alias') and expr.alias:
                                            col_name = expr.alias
                                        elif hasattr(expr, '__class__'):
                                            col_name = f"{expr.__class__.__name__}_{i}"
                                        
                                        # Add the found mutated column information to the list
                                        mutated_columns_index.append({
                                            'index': i,
                                            'name': col_name,
                                            'node': target_node,
                                            'stack_node_info': stack_node_info,
                                            'is_argument': True,
                                            'arg_index': expr.args.index(arg)
                                        })
            # If it's column_value_affected in the main query and both original and mutated results are provided, compare column values
            if branch_type == 'column_value_affected' and 'original_result' in comparison_result and 'mutated_result' in comparison_result:
                original_data = comparison_result['original_result']
                mutated_data = comparison_result['mutated_result']
                # Check if results are None
                if original_data is None and mutated_data is None:
                    comparison_result['is_expected'] = True
                    return 
                # Check if results are in table data format - Adapt to format: (((20, 1),), ['main_col_1', 'main_col_2'])
                if isinstance(original_data, tuple) and len(original_data) == 2 and isinstance(mutated_data, tuple) and len(mutated_data) == 2:
                    # Extract result data and column names
                    original_rows = original_data[0]
                    original_columns = original_data[1]
                    mutated_rows = mutated_data[0]
                    mutated_columns = mutated_data[1]
                    
                    # Check if column names are the same
                    if original_columns == mutated_columns:
                        comparison_result['has_same_columns'] = True
                        # Find columns containing aggregate functions
                        if mutated_columns:
                                # Initialize is_expected as True
                                comparison_result['is_expected'] = True
                                
                                # Check specific column values for each row
                                if isinstance(original_rows, tuple) and isinstance(mutated_rows, tuple):
                                    # Check if it's a nested multi-row result (e.g., ((10, 5000), (20, 6000)))
                                    if len(original_rows) > 0 and isinstance(original_rows[0], tuple) and \
                                       len(mutated_rows) > 0 and isinstance(mutated_rows[0], tuple):
                                        # Multi-row result: Iterate through each row for comparison
                                        if len(original_rows) != len(mutated_rows):
                                            comparison_result['is_expected'] = False
                                            
                                            # When row counts don't match, check if the mutated node is under a set operation node
                                            if self.node_info_stack:
                                                # Get the latest mutated node information from node_info_stack
                                                latest_node_info = self.node_info_stack[-1]
                                                mutated_node = latest_node_info.get('node')
                                                
                                                if mutated_node and self._check_node_in_set_operation(mutated_node):
                                                    # Convert set operation to UNION ALL
                                                    if self._convert_set_operation_to_union_all():
                                                        # Set flag indicating current mutation has been processed and needs to terminate
                                                        comparison_result['terminate_mutation'] = True
                                                        # Update is_expected to True since we've performed additional processing
                                                        comparison_result['is_expected'] = True
                                        else:
                                            # Create copies of original rows and mutated rows
                                            original_rows_list = list(original_rows)
                                            mutated_rows_list = list(mutated_rows)
                                            
                                            # First save the original unsorted results
                                            comparison_result['original_result'] = (tuple(original_rows_list), original_columns)
                                            comparison_result['mutated_result'] = (tuple(mutated_rows_list), mutated_columns)
                                              
                                            # First try to compare using unsorted results
                                            use_sorted_results = False
                                             
                                            # Check if row counts of unsorted results match
                                            if len(original_rows_list) != len(mutated_rows_list):
                                                use_sorted_results = True
                                                
                                                # When row counts don't match, check if the mutated node is under a set operation node
                                                if self.node_info_stack:
                                                    # Get the latest mutated node information from node_info_stack
                                                    latest_node_info = self.node_info_stack[-1]
                                                    mutated_node = latest_node_info.get('node')
                                                     
                                                    if mutated_node and self._check_node_in_set_operation(mutated_node):
                                                        # Convert set operation to UNION ALL
                                                        if self._convert_set_operation_to_union_all():
                                                            # Set flag indicating current mutation has been processed and needs to terminate
                                                            comparison_result['terminate_mutation'] = True
                                                            # Update is_expected to True since we've performed additional processing
                                                            comparison_result['is_expected'] = True
                                            else:
                                                # Initialize is_expected as True
                                                comparison_result['is_expected'] = True
                                                 
                                                # Compare unsorted results row by row
                                                for i in range(len(original_rows_list)):
                                                    original_row = original_rows_list[i]
                                                    mutated_row = mutated_rows_list[i]
                                                    # Generate signature
                                                     
                                                    original_signature = self.generate_signature(original_row, i, mutated_columns_index, original_sql)
                                                    mutated_signature = self.generate_signature(mutated_row, i, mutated_columns_index, original_sql)
                                                     
                                                    # Compare if signatures are equal
                                                    if original_signature != mutated_signature:
                                                        use_sorted_results = True
                                                        break
                                                      
                                                    # Extract values of columns to compare
                                                    for mutated_column in mutated_columns_index:
                                                        original_val = self.extract_value(original_row, mutated_column['index'])
                                                        mutated_val = self.extract_value(mutated_row, mutated_column['index'])
                                                         
                                                        # Convert numeric types to float with two decimal places, time types to seconds
                                                        from decimal import Decimal
                                                        from datetime import timedelta, datetime
                                                         
                                                        # Convert original value
                                                        if original_val is not None:
                                                            # Process numeric types
                                                            if isinstance(original_val, (int, float, Decimal)):
                                                                try:
                                                                    original_val = round(float(original_val), 2)
                                                                except:
                                                                    pass
                                                            # Process time types
                                                            elif isinstance(original_val, (timedelta, datetime)):
                                                                if isinstance(original_val, datetime):
                                                                    # Assume UTC time, convert to timestamp
                                                                    original_val = original_val.timestamp()
                                                                else:
                                                                # Convert timedelta to total seconds
                                                                    original_val = original_val.total_seconds()
                                                            elif isinstance(original_val, str):
                                                                try:
                                                                    # Try converting string to float
                                                                    float_value = float(original_val)
                                                                    # Round to two decimal places
                                                                    original_val = round(float_value, 2)
                                                                    
                                                                except ValueError:
                                                                    # Convert string to lowercase
                                                                    original_val = original_val.lower()
                                                            elif isinstance(original_val, bytes):
                                                                # Keep bytes object as is, use byte lexicographical order comparison (following MariaDB rules)
                                                                original_bytes = original_val  # Save original bytes value
                                                                try:
                                                                    original_val = str(original_val)[2:-1]
                                                                    original_val = float(original_val)
                                                                    original_val = round(original_val, 2)
                                                                except ValueError:
                                                                    original_val = original_bytes
                                                                    pass
                                                        # Convert mutated value
                                                        if mutated_val is not None:
                                                            # Process numeric types
                                                            if isinstance(mutated_val, (int, float, Decimal)):
                                                                try:
                                                                    mutated_val = round(float(mutated_val), 2)
                                                                except:
                                                                    pass
                                                            # Process time types
                                                            elif isinstance(mutated_val, (timedelta, datetime)):
                                                                if isinstance(mutated_val, datetime):
                                                                    # Assume UTC time, convert to timestamp
                                                                    mutated_val = mutated_val.timestamp()
                                                                else:
                                                                # Convert timedelta to total seconds
                                                                    mutated_val = mutated_val.total_seconds()
                                                            elif isinstance(mutated_val, str):
                                                                try:
                                                                    # Try converting string to float
                                                                    float_value = float(mutated_val)
                                                                    # Round to two decimal places
                                                                    mutated_val = round(float_value, 2)
                                                                    
                                                                except ValueError:
                                                                    # Convert string to lowercase
                                                                    mutated_val = mutated_val.lower()
                                                            elif isinstance(mutated_val, bytes):
                                                                # Keep bytes object as is, use byte lexicographical order comparison (following MariaDB rules)
                                                                original_bytes = mutated_val  # Save original bytes value
                                                                try:
                                                                    mutated_val = str(mutated_val)[2:-1]
                                                                    mutated_val = float(mutated_val)
                                                                    mutated_val = round(mutated_val, 2)
                                                                except ValueError:
                                                                    mutated_val = original_bytes  # Return to original bytes state on conversion failure
                                                        # Check if value change meets expectations
                                                        expected_change = True
                                                        if original_val is not None and mutated_val is not None:
                                                            direction = node_info.get('direction', None)
                                                             
                                                            if direction is not None:
                                                                if direction == 0:
                                                                    # direction=0 means expected mutated value to be smaller
                                                                    if mutated_val > original_val:
                                                                        expected_change = False
                                                                elif direction == 1:
                                                                    # direction=1 means expected mutated value to be larger
                                                                    if mutated_val < original_val:
                                                                        expected_change = False
                                                        elif original_val is None and mutated_val is None:
                                                            expected_change = True
                                                        else:
                                                            expected_change = False
                                                        # If value change doesn't meet expectations, try using sorted results
                                                        if not expected_change:
                                                            use_sorted_results = True
                                                            break
                                                    
                                                    # If comparison of mutated column values for current row fails, break out of loop
                                                    if use_sorted_results:
                                                        break
                                            
                                            # If unsorted comparison fails or row counts don't match, try using sorted results
                                            if use_sorted_results:
                                                # Sort original and mutated results
                                                try:
                                                    original_rows_sorted = sorted(original_rows_list, key=lambda row: self.get_sort_key(row, mutated_columns_index, original_sql))
                                                    mutated_rows_sorted = sorted(mutated_rows_list, key=lambda row: self.get_sort_key(row, mutated_columns_index, original_sql))
                                                    # Save sorted results to comparison_result dictionary
                                                    comparison_result['sorted_original_result'] = (tuple(original_rows_sorted), original_columns)
                                                    comparison_result['sorted_mutated_result'] = (tuple(mutated_rows_sorted), mutated_columns)
                                                except Exception as e:
                                                    # If sorting fails, use original order
                                                    original_rows_sorted = original_rows_list
                                                    mutated_rows_sorted = mutated_rows_list
                                                    # Save original results to comparison_result dictionary
                                                    comparison_result['sorted_original_result'] = original_data
                                                    comparison_result['sorted_mutated_result'] = mutated_data
                                                 
                                                # Check if sorted row counts match
                                                if len(original_rows_sorted) != len(mutated_rows_sorted):
                                                    comparison_result['is_expected'] = False
                                                    
                                                    # When row counts don't match, check if the mutated node is under a set operation node
                                                    if self.node_info_stack:
                                                        # Get the latest mutated node information from node_info_stack
                                                        latest_node_info = self.node_info_stack[-1]
                                                        mutated_node = latest_node_info.get('node')
                                                        
                                                        if mutated_node and self._check_node_in_set_operation(mutated_node):
                                                            # Convert set operation to UNION ALL
                                                            if self._convert_set_operation_to_union_all():
                                                                # Set flag indicating current mutation has been processed and needs to terminate
                                                                comparison_result['terminate_mutation'] = True
                                                                # Update is_expected to True since we've performed additional processing
                                                                comparison_result['is_expected'] = True
                                                else:
                                                    # Initialize is_expected as True
                                                    comparison_result['is_expected'] = True
                                                
                                                # Compare sorted results row by row
                                                for i in range(len(original_rows_sorted)):
                                                    original_row = original_rows_sorted[i]
                                                    mutated_row = mutated_rows_sorted[i]
                                                    # Generate signature
                                                    original_signature = self.generate_signature(original_row, i, mutated_columns_index, original_sql)
                                                    mutated_signature = self.generate_signature(mutated_row, i, mutated_columns_index, original_sql)
                                                    # Compare if signatures are equal
                                                    if original_signature != mutated_signature:
                                                        comparison_result['is_expected'] = False
                                                    # Extract values of columns to compare
                                                    for mutated_column in mutated_columns_index:
                                                        original_val = self.extract_value(original_row, mutated_column['index'])
                                                        mutated_val = self.extract_value(mutated_row, mutated_column['index'])
                                                        
                                                        # Convert numeric types to float with two decimal places, time types to seconds
                                                        from decimal import Decimal
                                                        from datetime import timedelta, datetime
                                                        
                                                        # Convert original value
                                                        if original_val is not None:
                                                            # Process numeric types
                                                            if isinstance(original_val, (int, float, Decimal)):
                                                                try:
                                                                    original_val = round(float(original_val), 2)
                                                                except:
                                                                    pass
                                                            # Process time types
                                                            elif isinstance(original_val, (timedelta, datetime)):
                                                                if isinstance(original_val, datetime):
                                                                    # Assuming UTC time, convert to timestamp
                                                                    original_val = original_val.timestamp()
                                                                else:
                                                                    # Convert timedelta to total seconds
                                                                    original_val = original_val.total_seconds()
                                                            elif isinstance(original_val, str):
                                                                try:
                                                                    # Try converting string to float
                                                                    float_value = float(original_val)
                                                                    # Round to two decimal places
                                                                    original_val = round(float_value, 2)
                                                                    
                                                                except ValueError:
                                                                    # Do not reverse string case, keep original string
                                                                    original_val = original_val.lower()
                                                                    pass
                                                            elif isinstance(original_val, bytes):
                                                                # Keep the bytes object itself, use byte lexicographical comparison 
                                                                original_val = original_bytes
                                                                try:
                                                                    original_val = str(original_val)[2:-1]
                                                                    original_val = float(original_val)
                                                                    original_val = round(original_val, 2)
                                                                except ValueError:
                                                                    # Do not reverse case for string, maintain original string
                                                                    original_val = original_bytes
                                                                    pass
                                                        # Convert mutated value
                                                        if mutated_val is not None:
                                                            # Handle numeric types
                                                            if isinstance(mutated_val, (int, float, Decimal)):
                                                                try:
                                                                    mutated_val = round(float(mutated_val), 2)
                                                                except:
                                                                    pass
                                                            # Handle time types
                                                            elif isinstance(mutated_val, (timedelta, datetime)):
                                                                if isinstance(mutated_val, datetime):
                                                                    # Assume UTC time, convert to timestamp
                                                                    mutated_val = mutated_val.timestamp()
                                                                else:
                                                                    # Convert timedelta to total seconds
                                                                    mutated_val = mutated_val.total_seconds()
                                                            elif isinstance(mutated_val, str):
                                                                try:
                                                                    # Try to convert string to float
                                                                    float_value = float(mutated_val)
                                                                    # Round to two decimal places
                                                                    mutated_val = round(float_value, 2)
                                                                    
                                                                except ValueError:
                                                                    # Do not reverse case for string, maintain original string
                                                                    mutated_val = mutated_val.lower()
                                                                    pass
                                                            elif isinstance(mutated_val, bytes):
                                                                # Keep the bytes object itself, use byte lexicographical comparison (following MariaDB rules)
                                                                mutated_bytes = mutated_val
                                                                try:
                                                                    mutated_val = str(mutated_val)[2:-1]
                                                                    mutated_val = float(mutated_val)
                                                                    mutated_val = round(mutated_val, 2)
                                                                except ValueError:
                                                                    # Do not reverse case for string, maintain original string
                                                                    mutated_val = mutated_bytes
                                                                    pass
                                                        # Check if value change meets expectations
                                                        if original_val is not None and mutated_val is not None:
                                                            expected_change = True
                                                            direction = node_info.get('direction', None)
                                                            func_name = node_info.get('func_name', '')
                                                             
                                                            if direction is not None:
                                                                # Special handling for stddev_samp mutation
                                                                if func_name == 'Variance':
                                                                    try:
                                                                        # Try to convert values to float for comparison
                                                                        original_float = float(original_val)
                                                                        mutated_float = float(mutated_val)
                                                                         
                                                                        if original_float < 1:
                                                                            # When original value is less than 1, original value should be greater than mutated value (mutated value should be smaller)
                                                                            if original_float > mutated_float:
                                                                                expected_change = False
                                                                        elif original_float >= 1:
                                                                            # When original value is greater than or equal to 1, original value should be less than mutated value (mutated value should be larger)
                                                                            if original_float < mutated_float:
                                                                                expected_change = False
                                                                    except (ValueError, TypeError):
                                                                        # If cannot convert to float, use default direction comparison
                                                                        if direction == 0:
                                                                            # direction=0 indicates expected mutated value to be smaller
                                                                            if mutated_val > original_val:
                                                                                expected_change = False
                                                                        elif direction == 1:
                                                                            # direction=1 indicates expected mutated value to be larger
                                                                            if mutated_val < original_val:
                                                                                expected_change = False
                                                                elif func_name == 'Stddev':
                                                                    # Special handling for StddevSamp function: treat None and 0.0 as consistent
                                                                    try:
                                                                        # Try to convert values to float for comparison
                                                                        original_float = float(original_val)
                                                                        mutated_float = float(mutated_val)
                                                                         
                                                                        if original_float < 1:
                                                                            # When original value is less than 1, original value should be greater than mutated value (mutated value should be smaller)
                                                                            if original_float < mutated_float:
                                                                                expected_change = False
                                                                        elif original_float >= 1:
                                                                            # When original value is greater than or equal to 1, original value should be less than mutated value (mutated value should be larger)
                                                                            if original_float > mutated_float:
                                                                                expected_change = False
                                                                    except (ValueError, TypeError):
                                                                        # If cannot convert to float, use default direction comparison
                                                                        if direction == 0:
                                                                            # direction=0 indicates expected mutated value to be smaller
                                                                            if mutated_val > original_val:
                                                                                expected_change = False
                                                                        elif direction == 1:
                                                                            # direction=1 indicates expected mutated value to be larger
                                                                            if mutated_val < original_val:
                                                                                expected_change = False
                                                                else:
                                                                    # Handle special value conversion cases for BitOr and BitAnd
                                                                    if func_name == 'BitOr' and (original_val == 18446744073709551615.0 or original_val == 1.84e+19) and mutated_val == 0.0:
                                                                        expected_change = True  # BITOR changing from all 1s to all 0s is considered expected
                                                                    elif func_name == 'BitAnd' and original_val == 0.0 and (mutated_val == 18446744073709551615.0 or mutated_val == 1.84e+19):
                                                                        expected_change = True  # BITAND changing from all 0s to all 1s is considered expected
                                                                    # Other mutation types use default direction comparison
                                                                    elif direction == 0:
                                                                        # direction=0 indicates expected mutated value to be smaller
                                                                        if mutated_val > original_val:
                                                                            expected_change = False
                                                                    elif direction == 1:
                                                                        # direction=1 indicates expected mutated value to be larger
                                                                        if mutated_val < original_val:
                                                                            expected_change = False
                                                        elif original_val is None and mutated_val is None:
                                                            expected_change = True
                                                        else:
                                                            expected_change = False
                                                        # Update is_expected status
                                                        if not expected_change:
                                                            comparison_result['is_expected'] = False
                                                            break
                                                                  
                    else:
                        comparison_result['is_expected'] = False
                        comparison_result['has_same_columns'] = False
                else:
                    comparison_result['is_expected'] = 'unknown'
        elif branch_type == 'same':
            # Same branch: requires original and mutated results to be identical

            
            # Set default expected result to None
            comparison_result['is_expected'] = None
            
            # If original and mutated results are provided, perform strict comparison
            if 'original_result' in comparison_result and 'mutated_result' in comparison_result:
                original_data = comparison_result['original_result']
                mutated_data = comparison_result['mutated_result']
                
                
                # Extract row data from original and mutated results
                original_rows = self.extract_rows(original_data)
                mutated_rows = self.extract_rows(mutated_data)
                
                # Compare row counts
                original_row_count = len(original_rows)
                mutated_row_count = len(mutated_rows)
                
    
                
                if original_row_count != mutated_row_count:
                    comparison_result['is_expected'] = False

                else:
                    # Parse SQL to get window function column indices
                    window_columns = []
                    try:
                        # Try to get SQL from comparison_result, otherwise use default value
                        sql = original_sql
                        if sql:
                            ast = sqlglot.parse_one(sql)
                            window_columns = self._find_all_window_functions(ast)
                    except Exception as e:
                        pass
                    
                    # Define sorting key generation function that excludes window function columns
                    def get_sort_key_excluding_window(row):
                        # Create sorting key, excluding window function columns
                        key = []
                        for col_idx, val in enumerate(row):
                            # Skip window function columns
                            if col_idx not in window_columns:
                                # Handle None and empty strings
                                if val is None or val == '':
                                    key.append((0,))
                                else:
                                    # Try to convert to numeric type for sorting
                                    try:
                                        key.append((1, float(val)))
                                    except (ValueError, TypeError):
                                        # Non-numeric types sorted as strings
                                        key.append((2, str(val)))
                        return tuple(key)
                    
                    # Perform sorting, excluding window function columns
                    sorted_original_rows = sorted(original_rows, key=get_sort_key_excluding_window)
                    sorted_mutated_rows = sorted(mutated_rows, key=get_sort_key_excluding_window)
                    
                    # Generate and compare row signatures
                    all_rows_match = True
                    for i in range(original_row_count):
                        original_row = sorted_original_rows[i]
                        mutated_row = sorted_mutated_rows[i]
                        
                        # Generate original row signature (excluding window function columns)
                        original_signature = self.generate_signature(original_row, i, [], sql)
                        # Generate mutated row signature (excluding window function columns)
                        mutated_signature = self.generate_signature(mutated_row, i, [], sql)
                        
                        # Compare signatures
                        if original_signature != mutated_signature:
                            all_rows_match = False
                            break
                    
                    if all_rows_match:
                        comparison_result['is_expected'] = True
                    else:
                        comparison_result['is_expected'] = False
        elif branch_type == None:
            comparison_result['is_expected'] = None


        
    def _get_operation_side_info(self, node):
        """Determine if node is the left or right operand of a set operation"""
        # Check if parent node is a set operation node
        if not hasattr(node, 'parent') or not node.parent:
            return None
        
        parent = node.parent
        parent_type = parent.__class__.__name__
        
        # Check if parent node is a set operation type
        if parent_type not in ['Union', 'Intersect', 'Except']:
            return None
        
        # Check if current node is left or right operand
        if hasattr(parent, 'left') and parent.left == node:
            return 'LEFT_OPERAND'
        elif hasattr(parent, 'right') and parent.right == node:
            return 'RIGHT_OPERAND'
        
        return None
        
    def _check_node_in_set_operation(self, node, ast=None):
        """Check if the mutated node is under a set operation node"""
        if not node:
            return False
        
        # If AST is not provided, use the current instance's AST
        if not ast:
            ast = self.ast
        
        # Check node's parent chain to see if there are any set operation nodes
        current_node = node
        while hasattr(current_node, 'parent') and current_node.parent:
            parent = current_node.parent
            parent_type = parent.__class__.__name__
            
            # Check if parent node is of set operation type
            if parent_type in ['Intersect', 'Except'] or (parent_type == 'Union' and parent.args['distinct'] == True):
                # Found set operation node, record it
                self.current_set_operation_node = parent
                return True
            
            current_node = parent
        
        return False
    
    def _convert_set_operation_to_union_all(self):
        """Convert set operation node to UNION ALL"""
        if not self.current_set_operation_node:
            return False
        set_node = self.current_set_operation_node
        set_type = set_node.__class__.__name__
        if set_type == 'Union':
            # For Union nodes, set args['distinct']=False to convert it to UNION ALL
            if hasattr(set_node, 'args'):
                # In sqlglot, the args dictionary of Union nodes has a distinct key, True means UNION (deduplicated), False means UNION ALL (non-deduplicated)
                set_node.args['distinct'] = False
            else:
                # Alternative approach for older versions of sqlglot
                if hasattr(set_node, 'set_all'):
                    set_node.set_all = True
                elif hasattr(set_node, 'all'):
                    set_node.all = True
            
            # Add the modified query to seedQuery.sql
            try:
                # Get the modified SQL query
                from sqlglot import transpile
                # Generate SQL string from root node
                if hasattr(self, 'root_node') and self.root_node:
                    modified_sql = self.root_node.sql(dialect=None)
                    # Write to seedQuery.sql file
                    seed_query_path = os.path.join('generated_sql', 'seedQuery.sql')
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(seed_query_path), exist_ok=True)
                    # Write to file in append mode
                    with open(seed_query_path, 'a', encoding='utf-8') as f:
                        f.write(f"{modified_sql};")
                        f.write('\n')
            except Exception as e:
                print(f"Failed to write modified query to seedQuery.sql: {e}")
            
            return True
        elif set_type in ['Intersect', 'Except']:
            # For Intersect and Except nodes, replace them with Union node and set all=True
            
            try:
                from sqlglot.expressions import Union
                # Create new Union node, passing left and right operands through constructor parameters
                # First check if set_node has left and right attributes
                left = set_node.this
                right = set_node.expression
                if hasattr(set_node, 'args') and 'with' in set_node.args:
                    with_ =set_node.args['with']
                else:
                    with_ = None
                new_union_node = Union(this=left, expression=right)
                new_union_node.args['with']=with_
                # In sqlglot, the args dictionary of Union nodes has a distinct key, True means UNION (deduplicated), False means UNION ALL (non-deduplicated)
                if hasattr(new_union_node, 'args'):
                    # Set distinct=False to create UNION ALL
                    new_union_node.args['distinct'] = False
                # Replace original node
                set_node.replace(new_union_node)
                root=self.get_root_node(new_union_node)
                # Add the modified query to seedQuery.sql
                try:
                    # Get the modified SQL query
                    from sqlglot import transpile
                    # Generate SQL string from root node
                    if root:
                        modified_sql = root.sql(dialect=None)
                        # Write to seedQuery.sql file
                        seed_query_path = os.path.join('generated_sql', 'seedQuery.sql')
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(seed_query_path), exist_ok=True)
                        # Write to file in append mode
                        with open(seed_query_path, 'a', encoding='utf-8') as f:
                            f.write(f"{modified_sql}\n")
                except Exception as e:
                    print(f"Failed to write modified query to seedQuery.sql: {e}")
                
                return True
            except Exception as e:
                print(f"Failed to convert set operation: {e}")
                return False
        
        return False
        
    def get_root_node(self, node):
        """Get the root node from any node
        
        Args:
            node: Any AST node
            
        Returns:
            Root node object
        """
        current = node
        # Continuously traverse parent nodes upward until finding the node without a parent (root node)
        while current and hasattr(current, 'parent') and current.parent:
            current = current.parent
        return current
    
    def _get_query_index_info(self, sql_query):
        """Execute EXPLAIN SQL to get index usage information for the query
        
        Args:
            sql_query: SQL query to analyze
            
        Returns:
            str: Text description of index usage
        """
        try:
            # Directly import and use SeedQueryGenerator class from get_seedQuery
            from get_seedQuery import SeedQueryGenerator
            
            # Create SeedQueryGenerator instance
            seed_generator = SeedQueryGenerator()
            
            # Use SeedQueryGenerator's connect_db method to get database connection
            connection = seed_generator.connect_db()
            
            if not connection:
                return "Failed to establish database connection"
            
            # Execute EXPLAIN query
            explain_sql = f"EXPLAIN {sql_query}"
            cursor = connection.cursor()
            cursor.execute(explain_sql)
            
            # Get results
            explain_results = cursor.fetchall()
            
            # Close cursor and connection
            cursor.close()
            connection.close()
            
            # Format results as readable text
            index_info = []
            
            # Try to get column names
            try:
                column_names = [desc[0] for desc in cursor.description]
                for row in explain_results:
                    row_info = []
                    for i, value in enumerate(row):
                        if i < len(column_names):
                            row_info.append(f"{column_names[i]}: {value}")
                        else:
                            row_info.append(str(value))
                    index_info.append(", ".join(row_info))
            except:
                # If unable to get column names, format row data directly
                for row in explain_results:
                    index_info.append(str(row))
            
            return "\n".join(index_info)
            
        except Exception as e:
            return f"Failed to execute EXPLAIN: {str(e)}"
    
    def _log_invalid_mutation(self, original_sql, mutated_sql, original_result, 
                           mutated_result, node_info, reason):
        """Log invalid aggregation function mutations to log file
        
        Includes index usage information of the original query (obtained through EXPLAIN SQL)
        """
        try:
            # Get current database dialect
            from data_structures.db_dialect import get_current_dialect
            dialect = get_current_dialect()
            db_type = dialect.name.upper()
        except (ImportError, AttributeError):
            # If database dialect cannot be obtained, default to GENERIC
            db_type = 'GENERIC'
            
        # Create invalid_mutation/{db_type} folder if it doesn't exist
        log_dir = f'invalid_mutation/{db_type}'
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        # Get current class name as part of the log filename, and add mutation category
        class_name = self.__class__.__name__
        
        # Determine mutation category
        mutation_category = "Aggregation Function Mutation"
        
        # Get function name and mutation direction
        func_name = node_info.get('func_name', 'Unknown')
        direction = node_info.get('direction', 'Unknown')
        
        # Generate log filename
        log_filename = f"{log_dir}/{class_name}_{db_type}_invalid_mutations.log"
        
        # Extract result sets and column names
        original_result_data = original_result[0] if original_result and len(original_result) > 0 else []
        original_column_names = original_result[1] if original_result and len(original_result) > 1 else []
        
        mutated_result_data = mutated_result[0] if mutated_result and len(mutated_result) > 0 else []
        mutated_column_names = mutated_result[1] if mutated_result and len(mutated_result) > 1 else []
        
        # Get index usage information for the original query
        index_info = self._get_query_index_info(original_sql)
        
        # Write to log
        with open(log_filename, 'a', encoding='utf-8') as f:
            f.write(f"=== {mutation_category} Result Mismatch ({db_type}) ===\n")
            f.write(f"Function Name: {func_name}\n")
            f.write(f"Mutation Direction: {direction}\n")
            f.write(f"Original SQL: {original_sql}\n")
            f.write(f"Mutated SQL: {mutated_sql}\n")
            f.write(f"Original Query Index Usage:\n{index_info}\n")
            f.write(f"Original Result Set Size: {len(original_result_data)}\n")
            f.write(f"Mutated Result Set Size: {len(mutated_result_data)}\n")
            f.write(f"Original Column Names: {original_column_names}\n")
            f.write(f"Mutated Column Names: {mutated_column_names}\n")
            f.write(f"Failure Reason: {reason}\n")
            f.write(f"Original Result Set: {original_result_data}\n")
            f.write(f"Mutated Result Set: {mutated_result_data}\n\n")
        
        print(f"Logged unexpected mutation to log file: {log_filename}")