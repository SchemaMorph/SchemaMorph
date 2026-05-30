import sqlglot
import random
import copy
import os
import string

class SetMutator:
    """Class for performing mutation operations on SQL AST"""
    def __init__(self, ast, db_type='UNKNOWN'):
        self.ast = ast
        self.db_type = db_type.upper()  # Store database dialect
        self.mutable_nodes = []  # Store mutable nodes
        self.mutated = False  # Flag indicating whether mutation has been performed
        self.temp_original_nodes = {}  # Temporarily store original node information

    def findnode(self, ast):
        """Identify all mutable nodes in the AST"""
        if not ast:
            return

        # Clear previous results
        self.mutable_nodes = []
        # Root node default flag is 1 (positive)
        self._find_mutable_nodes(ast, parent_flag=1)
        return self.mutable_nodes

    def _find_mutable_nodes(self, node, parent_flag=1, is_in_on_condition=False):
        """Recursively find mutable nodes, supporting flag passing and inversion"""
        if not node:
            return

        # Current node's flag initially inherits from parent node
        current_flag = parent_flag
        
        # Process Not node, invert flag
        if node.__class__.__name__ == 'Not':
            current_flag = parent_flag ^ 1  # Invert flag
        # Process Except node, invert flag

        # Process various node types, passing current flag value
        if node.__class__.__name__ == 'Select':
            self._process_select(node, current_flag)
        elif node.__class__.__name__ == 'Where':
            self._process_where(node, current_flag)
        elif node.__class__.__name__ == 'Having':
            self._process_having(node, current_flag)
        elif node.__class__.__name__ == 'Join':
            # First process INNER JOIN mutation
            if hasattr(node, 'args') and node.args.get('kind') == 'INNER':
                self._process_inner_join(node, current_flag)
            # Then process general JOIN node (ON condition mutation)
            self._process_join(node, current_flag)
        elif node.__class__.__name__ == 'Like':
            self._process_like(node, current_flag)
        elif node.__class__.__name__ == 'RegExp':
            self._process_regexp(node, current_flag)
        elif node.__class__.__name__ == 'Gt' or node.__class__.__name__ == 'Lt' or \
             node.__class__.__name__ == 'Gte' or node.__class__.__name__ == 'Lte' or \
             node.__class__.__name__ == 'Eq':
            self._process_comparison(node, current_flag)
        elif node.__class__.__name__ == 'And':
            self._process_logical_and(node, current_flag)
        elif node.__class__.__name__ == 'Intersect':
            self._process_intersect(node, current_flag)
        elif node.__class__.__name__ == 'Except':
            self._process_except(node, current_flag)
        #elif node.__class__.__name__ == 'Union':
        #    self._process_union(node, current_flag)

        # Manually check node attributes to recursively process child nodes, ensuring correct flag passing
        if hasattr(node, 'args'):
            # Manually iterate through values in the node's args dictionary
            for arg_name, arg_value in node.args.items():
                # Special handling for the expression attribute of Except nodes
                if node.__class__.__name__ == 'Except' and arg_name == 'expression':
                    # The right expression of Except nodes needs to reverse the flag again
                    right_flag = current_flag ^ 1
                    self._process_node_recursive(arg_value, right_flag)
                # Special handling for the this attribute (usually conditional expressions)
                elif arg_name == 'this':
                    # Check if it's an IN node and in an ON condition
                    is_in_node = node.__class__.__name__ == 'In'
                    # For the right attribute of IN nodes, do not recursively search if in an ON condition
                    if is_in_node and is_in_on_condition and arg_name == 'this' and 'query' in node.args:
                        # Do not process queries on the right side of IN nodes
                        continue
                    self._process_node_recursive(arg_value, current_flag)
                # Process the expressions attribute (usually contains selection lists, expression lists, etc.)
                elif arg_name == 'expressions' and isinstance(arg_value, (list, tuple)):
                    for expr in arg_value:
                        self._process_node_recursive(expr, current_flag)
                # Process the on attribute of Join nodes, marking as ON condition
                elif node.__class__.__name__ == 'Join' and arg_name == 'on':
                    # For the on condition of Join nodes, pass the marker
                    self._process_node_recursive(arg_value, current_flag, is_in_on_condition=True)
                # Process the right attribute of IN nodes, do not recursively search if in an ON condition
                elif node.__class__.__name__ == 'In' and is_in_on_condition and arg_name == 'query':
                    # Do not process queries after IN operators in ON conditions
                    continue
                # Process other attributes that may contain child nodes
                elif arg_name not in ['distinct', 'by_name', 'side', 'kind', 'alias', 'join_type']:
                    # Pass the current state of whether it's in an ON condition
                    self._process_node_recursive(arg_value, current_flag, is_in_on_condition)

    def _process_select(self, node, flag):
        """Process SELECT node"""
        if hasattr(node, 'args') and 'distinct' in node.args:
            # Record DISTINCT mutation point
            distinct_value = node.args['distinct']
            if distinct_value is not None:
                node_info = {
                    'node_id': id(node),
                    'node': node,
                    'mutation_type': 'FixMDistinct',
                    'attr_name': 'args',
                    'sub_attr': 'distinct',
                    'direction': 0,  # Default direction, indicates result reduction
                    'flag': flag  # Use inherited flag value
                }
                self.mutable_nodes.append(node_info)

    def _process_where(self, node, flag):
        """Process WHERE node"""
        node_info = {
            'node_id': id(node),
            'node': node,
            'mutation_type': 'FixMWhere',
            'attr_name': 'this',
            'direction': 1,  # Default direction, indicates result expansion
            'flag': flag  # Use inherited flag value
        }
        self.mutable_nodes.append(node_info)

    def _process_having(self, node, flag):
        """Process HAVING node"""
        node_info = {
            'node_id': id(node),
            'node': node,
            'mutation_type': 'FixMHaving',
            'attr_name': 'this',
            'direction': 1,  # Default direction, indicates result expansion
            'flag': flag  # Use inherited flag value
        }
        self.mutable_nodes.append(node_info)

    def _process_join(self, node, flag):
        """Process JOIN node"""
        if hasattr(node, 'args') and 'on' in node.args:
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'FixMOn',
                'attr_name': 'args',
                'sub_attr': 'on',
                'direction': 1,  # Default direction, indicates result expansion
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)

    def _process_like(self, node, flag):
        """Process LIKE node"""
        if hasattr(node, 'args') and 'pattern' in node.args:
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'RdMLike',
                'attr_name': 'args',
                'sub_attr': 'pattern',
                'direction': 1,  # Default direction, indicates result expansion
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)

    def _process_regexp(self, node, flag):
        """Process REGEXP node"""
        if hasattr(node, 'args') and 'pattern' in node.args:
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'RdMRegExp',
                'attr_name': 'args',
                'sub_attr': 'pattern',
                'direction': 1,  # Default direction, indicates result expansion
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)

    def _process_comparison(self, node, flag):
        """Process comparison operator node"""
        # Determine current operator based on node type
        op_map = {
            'Gt': 'gt',
            'Lt': 'lt',
            'Gte': 'gte',
            'Lte': 'lte',
            'Eq': 'eq'
        }
        node_class = node.__class__.__name__
        if node_class in op_map:
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'FixMCmpOp',
                'attr_name': 'op',
                'direction': 1,  # Default direction, indicates result expansion
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)

    def _process_union(self, node):
        """Process UNION node"""
        if hasattr(node, 'args') and 'distinct' in node.args:
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'FixMUnionAll',
                'attr_name': 'args',
                'sub_attr': 'distinct',
                'direction': 1,  # Default direction, indicates result expansion
                'flag': 1  # Flag bit, 1 means positive
            }
            self.mutable_nodes.append(node_info)
    
    def _process_logical_and(self, node, flag):
        """Process logical AND expression node"""
        # SQLGlot's AND expression uses this and expression attributes to store left and right operands
        # When AND connects two conditions, there are two sub-expressions that can be mutated
        if hasattr(node, 'this') and hasattr(node, 'expression'):
            # Check if there are two valid sub-expressions
            if node.this is not None and node.expression is not None:
                node_info = {
                    'node_id': id(node),
                    'node': node,
                    'mutation_type': 'FixMLogicalAnd',
                    'attr_name': 'expression',  # Modify the expression attribute to remove the second condition
                    'direction': 1,  # Expand result: removing part of the condition will get more results
                    'flag': flag  # Use inherited flag value
                }
                self.mutable_nodes.append(node_info)
    
    def _process_inner_join(self, node, flag):
        """Process INNER JOIN node"""
        if hasattr(node, 'args') and 'kind' in node.args:
            current_kind = node.args['kind']
            if current_kind == 'INNER':
                node_info = {
                    'node_id': id(node),
                    'node': node,
                    'mutation_type': 'FixMInnerJoin',
                    'attr_name': 'args',
                    'sub_attr': 'kind',
                    'direction': 1,  # Expand result: INNER JOIN -> LEFT JOIN will get more results
                    'flag': flag  # Use inherited flag value
                }
                self.mutable_nodes.append(node_info)
    
    def _process_intersect(self, node, flag):
        """Process INTERSECT node"""
        if hasattr(node, 'args'):
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'FixMIntersect',
                'attr_name': None,  # Directly replace the entire node
                'direction': 1,  # Expand result: INTERSECT -> UNION will get more results
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)
    
    def _process_except(self, node, flag):
        """Process EXCEPT node"""
        if hasattr(node, 'args'):
            node_info = {
                'node_id': id(node),
                'node': node,
                'mutation_type': 'FixMExcept',
                'attr_name': None,  # Directly replace the entire node
                'direction': 1,  # Expand result: A EXCEPT B -> A will get more results
                'flag': flag  # Use inherited flag value
            }
            self.mutable_nodes.append(node_info)
            
    def _process_node_recursive(self, node_value, flag, is_in_on_condition=False):
        """Recursively process node values, support various data types, add special handling for IN nodes in ON conditions"""
        if node_value is None:
            return
        
        # If it's a node object (has args attribute), process recursively
        if hasattr(node_value, 'args'):
            self._find_mutable_nodes(node_value, flag, is_in_on_condition)
        # If it's a list or tuple, recursively process each element
        elif isinstance(node_value, (list, tuple)):
            for item in node_value:
                self._process_node_recursive(item, flag, is_in_on_condition)

    def mutate(self, executor):
        """Perform mutation operations on the AST"""
        if self.mutated:
            return self.ast

        # Find all mutable nodes
        self.findnode(self.ast)

        # If there are no mutable nodes, return directly
        if not self.mutable_nodes:
            return self.ast

        # Save a deep copy of the original AST for result comparison
        initial_ast = copy.deepcopy(self.ast)
        sql = str(initial_ast)

        # Execute the original SQL query to get initial results
        result_data = executor.execute_query(sql)
        # Unpack result data
        if isinstance(result_data, tuple) and len(result_data) == 2:
            result = result_data[0]  # Result set
            original_column_names = result_data[1]  # Column name list
        else:
            result = result_data
            original_column_names = []

        # Try to mutate each mutable node
        all_mutable_nodes = self.mutable_nodes.copy()
        for item in all_mutable_nodes:
            # Start each mutation from the original AST
            self.ast = copy.deepcopy(initial_ast)
            temp_ast = self.ast
            self.findnode(temp_ast)
            current_mutable_nodes = self.mutable_nodes

            # Find the node at the corresponding position
            original_index_in_all = all_mutable_nodes.index(item)
            original_pos = -1

            # Method 1: Try to match using node_id
            for i, n in enumerate(current_mutable_nodes):
                if n['node_id'] == item['node_id']:
                    original_pos = i
                    break

            # Method 2: If node_id doesn't match, try to match using mutation_type and attr_name
            if original_pos == -1:
                # Get all nodes of the same type and attribute
                same_type_nodes = [n for n in current_mutable_nodes if 
                                n['mutation_type'] == item['mutation_type'] and 
                                n['attr_name'] == item['attr_name']]
                
                # Get all nodes of the same type in the original list
                original_same_type = [n for n in all_mutable_nodes if 
                                    n['mutation_type'] == item['mutation_type'] and 
                                    n['attr_name'] == item['attr_name']]
                
                # Calculate relative position
                if original_same_type and same_type_nodes:
                    same_type_indices = [i for i, n in enumerate(all_mutable_nodes) if \
                                        n['mutation_type'] == item['mutation_type'] and \
                                        n['attr_name'] == item['attr_name']]
                    if original_index_in_all in same_type_indices:
                        original_pos = same_type_indices.index(original_index_in_all)

            # If still not found, try to use node content features
            if original_pos == -1:
                for i, n in enumerate(original_same_type):
                    # For UNION nodes, check distinct value as a feature
                    if (hasattr(n['node'], 'args') and 'distinct' in n['node'].args and \
                        hasattr(item['node'], 'args') and 'distinct' in item['node'].args):
                        if n['node'].args['distinct'] == item['node'].args['distinct']:
                            original_pos = i
                            break

            # If all methods fail, use default position
            if original_pos == -1:
                # In the loop, we assign an incremental index to each item
                # This ensures each item can match a different node
                item_index = all_mutable_nodes.index(item) if item in all_mutable_nodes else 0
                original_pos = item_index % len(same_type_nodes)

            # Select the corresponding node in the current list based on relative position
            if 0 <= original_pos < len(same_type_nodes):
                item = same_type_nodes[original_pos]
            else:
                # Default to the first node
                item = same_type_nodes[0]

            # Since we've already ensured item has a value, no need to check if item is None here
            # Execute mutation
            self.ast = self._mutate_node(item)

            # Calculate final_direction before executing SQL query
            final_direction=(item['direction']^item['flag'])^1

            mutated_sql = self.ast.sql()
            #transformed_sql = sqlglot.transpile(mutated_sql, dialect='mysql')[0]
            transformed_result_data=executor.execute_query(mutated_sql)
            # Unpack mutated result data
            if isinstance(transformed_result_data, tuple) and len(transformed_result_data) == 2:
                transformed_result = transformed_result_data[0]  # Result set
                transformed_column_names = transformed_result_data[1]  # Column name list
            else:
                transformed_result = transformed_result_data
                transformed_column_names = []
            # No need to recalculate final_direction as item['direction'] and item['flag'] haven't changed

            # Compare result set sizes
            original_result_size = len(result) if result else 0
            mutated_result_size = len(transformed_result) if transformed_result else 0

            # Convert result sets to lists (preserving duplicate elements and original order) for membership comparison
            # Now results are lists of tuples, process these tuples directly
            original_result_list = []
            if result:
                # Use tuples directly as comparison units, preserving original order
                for row in result:
                    original_result_list.append(row)

            mutated_result_list = []
            if transformed_result:
                # Use tuples directly as comparison units, preserving original order
                for row in transformed_result:
                    mutated_result_list.append(row)

            # Optimized subset check function that directly compares list elements to check sub/superset relationships
            def is_list_subset(small_list, large_list, small_columns, large_columns):
                """Check if small_list is a subset of large_list, comparing each element exactly while considering column names"""
                # First check length relationship
                if len(small_list) > len(large_list):
                    return False

                # Check if column names are consistent
                if small_columns and large_columns and small_columns != large_columns:
                    return False

                temp_large = large_list.copy()

                # Try to match each element in the small list
                for item in small_list:
                    if item in temp_large:
                        # Remove matched element
                        temp_large.remove(item)
                    else:
                        return False

                # All elements found matches
                return True
            # Check result set relationships
            is_valid = False
            if final_direction == 1:
                 # Expand results: Mutated results should be greater than or equal to original results, and original result set is a subset of mutated result set (considering duplicates and column names)
                size_valid = (original_result_size == 0) or (mutated_result_size >= original_result_size)
                subset_valid = True if original_result_size == 0 else is_list_subset(original_result_list, mutated_result_list, original_column_names, transformed_column_names)
                is_valid = size_valid and subset_valid
            else:
                # Shrink results: Original results should be greater than or equal to mutated results, and mutated result set is a subset of original result set (considering duplicates and column names)
                size_valid = (mutated_result_size == 0) or (original_result_size >= mutated_result_size)
                subset_valid = True if mutated_result_size == 0 else is_list_subset(mutated_result_list, original_result_list, transformed_column_names, original_column_names)
                is_valid = size_valid and subset_valid

            # If not as expected, record SQL pairs
            if not is_valid:
                # Create invalid_mutation folder if it doesn't exist
                import os
                
                # Get current database dialect
                db_type = self.db_type
                
                # Create log directory categorized by dialect
                log_dir = os.path.join('invalid_mutation', db_type)
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir)

                # Get current class name as part of the log filename
                class_name = self.__class__.__name__
                log_filename = os.path.join(log_dir, f"{class_name}_{db_type}_invalid_mutations.log")

                # Get index usage information for the original query
                def _get_query_index_info(self, sql_query):
                    """Execute EXPLAIN SQL to get query index usage information
                    
                    Parameters:
                        sql_query: The SQL query to analyze
                    
                    Returns:
                        str: Text description of index usage
                    """
                    try:
                        # Directly import and use the SeedQueryGenerator class from get_seedQuery
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
                        
                        # Format results into readable text
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
                            # If column names cannot be obtained, format row data directly
                            for row in explain_results:
                                index_info.append(str(row))
                        
                        return "\n".join(index_info)
                        
                    except Exception as e:
                        return f"EXPLAIN execution failed: {str(e)}"
                
                try:
                    index_info = _get_query_index_info(self, sql)
                except Exception as e:
                    index_info = f"Failed to get index information: {str(e)}"
                
                # Write to log
                with open(log_filename, 'a', encoding='utf-8') as f:
                    f.write(f"=== Unexpected Mutation ===\n")
                    f.write(f"Original SQL: {sql}\n")
                    f.write(f"Mutated SQL: {mutated_sql}\n")
                    f.write(f"Original query index usage:\n{index_info}\n")
                    f.write(f"Original result set size: {original_result_size}\n")
                    f.write(f"Mutated result set size: {mutated_result_size}\n")
                    f.write(f"Original column names: {original_column_names}\n")
                    f.write(f"Mutated column names: {transformed_column_names}\n")
                    f.write(f"final_direction: {final_direction}\n")
                    f.write(f"direction: {item['direction']}\n")
                    f.write(f"flag: {item['flag']}\n")
                    f.write(f"Original result set: {original_result_list}\n")
                    f.write(f"Mutated result set: {mutated_result_list}\n")
                    f.write(f"Mutation type: {item['mutation_type']}\n")
                    
                    if final_direction == 1:
                        f.write(f"Failure reason: Original result set is not a subset of mutated result set or mutated result set size is smaller than original result set\n")
                    else:
                        f.write(f"Failure reason: Mutated result set is not a subset of original result set or original result set size is smaller than mutated result set\n")
                    
                    f.write(f"Is original result set empty: {original_result_size == 0}\n")
                    f.write(f"Is mutated result set empty: {mutated_result_size == 0}\n\n")

            if final_direction==1:
                pass  # Additional logic for final_direction==1 can be added here
        self.mutated = False
        return self.ast

    def _mutate_node(self, item):
        self.mutated =True
        """Perform mutation operation on a single node, temporarily store original information before mutation"""
        mutation_type = item['mutation_type']
        node = item['node']
        attr_name = item['attr_name']
        direction = item['direction']
        flag = item['flag']  # 1: positive, 0: negative
        node_id = item['node_id']
        # Temporarily store original node information
        if node_id not in self.temp_original_nodes:
            if attr_name and hasattr(node, attr_name) and not isinstance(getattr(node, attr_name), (sqlglot.expressions.Expression)):
                # For simple attributes, store attribute values
                self.temp_original_nodes[node_id] = {
                    'node': node,
                    'attr_name': attr_name,
                    'original_value': getattr(node, attr_name),
                    'is_replaced': False
                }

            else:
                # For complex expressions or attributes that cannot be set directly, store the entire node
                self.temp_original_nodes[node_id] = {
                    'node': node,
                    'attr_name': attr_name,
                    'original_value': node,
                    'is_replaced': True
                }

        # First calculate final_direction, which will be recalculated later for FixMDistinct type
        final_direction=(item['direction']^item['flag'])^1

        # Perform mutation operation
        if mutation_type == 'FixMDistinct':
            # Check if there is a sub_attr parameter (used to access specific keys in the args dictionary)
            sub_attr = item.get('sub_attr')
            if sub_attr and hasattr(node, 'args'):
                # Get current distinct status
                distinct_value = node.args.get(sub_attr)
                if distinct_value is None:
                    # Add DISTINCT: Mutated result set is deduplicated and should be a subset of the original result set
                    item['direction'] = 0
                    node.args[sub_attr] = 'Distinct'
                else:
                    # Remove DISTINCT: Original result set is deduplicated and should be a subset of the mutated result set
                    item['direction'] = 1
                    node.args[sub_attr] = None

            # Recalculate final_direction after setting item['direction']
            final_direction=(item['direction']^item['flag'])^1

        elif mutation_type == 'FixMCmpOp':
            # Modify comparison operator: Only keep special handling for greater than symbol (gt)
            current_op = getattr(node, attr_name)
            
            # Handling for greater than symbol (gt): Modify the constant value
            if current_op in ['gt', 'gte']:
                # Try to get the value of the right-hand expression
                try:
                    # Check if the right-hand side is a Literal node
                    if hasattr(node, 'expression') and hasattr(node.expression, 'this'):
                        # Try to convert the value to a number
                        value = node.expression.this
                        if isinstance(value, (int, float)):
                            # Modify to a smaller constant, e.g., x > 10 → x > 5, which expands the result set
                            # Generate a smaller constant, e.g., half of the original value (floor division)
                            if value > 0:
                                new_value = max(0, int(value / 2))
                            else:
                                new_value = value * 2  # For negative numbers, multiplying by 2 gives a smaller value
                            
                            # Create new Literal node
                            new_literal = sqlglot.expressions.Literal(this=new_value, is_string=False)
                            # Replace right-hand expression
                            node.expression = new_literal
                            item['direction'] = 1  # Expand result set
                except Exception as e:
                    pass 

            elif current_op in ['lt', 'lte']:
                # Try to get the value of the right-hand expression
                try:
                    # Check if the right-hand side is a Literal node
                    if hasattr(node, 'expression') and hasattr(node.expression, 'this'):
                        # Try to convert the value to a number
                        value = node.expression.this
                        if isinstance(value, (int, float)):
                            # Modify to a larger constant, e.g., x < 10 → x < 20, which shrinks the result set
                            # Generate a larger constant, e.g., twice the original value (rounded up)
                            if value > 0:
                                new_value = int(value * 2)
                            else:
                                new_value = max(-1, int(value / 2))  # For negative numbers, dividing by 2 gives a smaller value
                            
                            # Create new Literal node
                            new_literal = sqlglot.expressions.Literal(this=new_value, is_string=False)
                            # Replace right-hand expression
                            node.expression = new_literal
                            item['direction'] = 1  # Shrink result set

                except Exception as e:
                    pass  
            
        elif mutation_type == 'FixMUnionAll':
            # Toggle between UNION and UNION ALL
            # In sqlglot, the Union node's args dictionary has a distinct key, True means UNION (deduplicated), False means UNION ALL (not deduplicated)

            # Check if there is a sub_attr parameter (used to access specific keys in the args dictionary)
            sub_attr = item.get('sub_attr')
            if sub_attr and hasattr(node, 'args') and sub_attr in node.args:
                current_distinct = node.args[sub_attr]
                operation_type = 'UNION' if current_distinct else 'UNION ALL'

                # Toggle the value of the distinct attribute to switch between UNION and UNION ALL
                if flag == 1 or True:  # Always allow switching, no longer restricted by flag
                    new_distinct = not current_distinct
                    node.args[sub_attr] = new_distinct
                    new_operation_type = 'UNION' if new_distinct else 'UNION ALL'

                    # Set direction based on switching direction to ensure correct result set size relationship
                    # UNION -> UNION ALL: Result set may grow (including duplicates)
                    # UNION ALL -> UNION: Result set may shrink (removing duplicates)
                    if current_distinct and not new_distinct:  # UNION -> UNION ALL
                        item['direction'] = 1  # Expand result
                    elif not current_distinct and new_distinct:  # UNION ALL -> UNION
                        item['direction'] = 0  # Shrink result
        elif mutation_type == 'FixMOn':
            # Modify JOIN's ON condition
            if hasattr(node, 'args') and 'on' in node.args and 'kind' in node.args:
                original_on = node.args['on']
                if direction == 1:
                    # Expand results: ON xxx -> ON 1=1
                    new_on = sqlglot.expressions.Paren(
                        this=sqlglot.expressions.EQ(
                            this=sqlglot.expressions.Literal(this='1', is_string=True),
                            expression=sqlglot.expressions.Literal(this='1', is_string=True)
                        )
                    )

                elif direction==0:
                    # Shrink results: ON xxx -> ON 1=0
                    new_on = sqlglot.expressions.Paren(
                        this=sqlglot.expressions.EQ(
                            this=sqlglot.expressions.Literal(this='1', is_string=True),
                            expression=sqlglot.expressions.Literal(this='0', is_string=True)
                        )
                    )


                try:
                    # Try to directly replace the ON node in args
                    node.args['on'] = new_on
                    # Force update the key attribute of the node (if exists)
                    if hasattr(node, 'key'):
                        node.key = f"JOIN_ON_1_{'=' if final_direction == 1 else '!='}_1"
                except Exception as e:
                    pass  # Failed to replace ON clause
            else:
                pass  # Node has no 'on' parameter
        elif mutation_type == 'FixMWhere':
            # Modify WHERE condition
            if direction == 1:
                new_where = sqlglot.expressions.Where(
                    this=sqlglot.expressions.EQ(
                        this=sqlglot.expressions.Literal(this='1', is_string=True),
                        expression=sqlglot.expressions.Literal(this='1', is_string=True)
                    )
                )
                node.replace(new_where)

            elif direction==0:
                new_where = sqlglot.expressions.Where(
                    this=sqlglot.expressions.EQ(
                        this=sqlglot.expressions.Literal(this='1', is_string=True),
                        expression=sqlglot.expressions.Literal(this='0', is_string=True)
                    )
                )
                node.replace(new_where)
        elif mutation_type == 'FixMHaving':
            # Modify HAVING condition
            if direction == 1:
                # Expand results: HAVING xxx -> HAVING 1=1
                from sqlglot.expressions import EQ
                new_having = sqlglot.expressions.Having(
                    this=EQ(
                        this=sqlglot.expressions.Literal(this='1', is_string=True),
                        expression=sqlglot.expressions.Literal(this='1', is_string=True)
                    )
                )
                node.replace(new_having)
            elif direction==0:
                # Shrink results: HAVING xxx -> HAVING 1=0
                from sqlglot.expressions import EQ
                new_having = sqlglot.expressions.Having(
                    this=EQ(
                        this=sqlglot.expressions.Literal(this='1', is_string=True),
                        expression=sqlglot.expressions.Literal(this='0', is_string=True)
                    )
                )
                node.replace(new_having)
        elif mutation_type == 'RdMLike':
            # Modify LIKE expression pattern
            if hasattr(node, 'args') and 'pattern' in node.args:
                original_pattern = node.args['pattern']
                pattern_str = original_pattern.this if hasattr(original_pattern, 'this') else str(original_pattern)

                try:
                    # Remove possible single quotes
                    if pattern_str.startswith("'"):
                        pattern_str = pattern_str[1:]
                    if pattern_str.endswith("'"):
                        pattern_str = pattern_str[:-1]

                    if direction == 1:
                        # Expand results: Replace normal characters with wildcards, or replace '_' with '%'
                        new_pattern = []
                        for char in pattern_str:
                            if char == '_':
                                new_pattern.append('%')
                            elif random.random() < 0.3 and char.isalnum():
                                # 30% probability of replacing ordinary characters with wildcard characters
                                new_pattern.append(random.choice(['_', '%']))
                            else:
                                new_pattern.append(char)
                        new_pattern_str = ''.join(new_pattern)
                    else:
                        # Shrink results: Replace '%' with '_' or fixed characters
                        new_pattern = []
                        for char in pattern_str:
                            if char == '%':
                                if random.random() < 0.5:
                                    new_pattern.append('_')
                                else:
                                    new_pattern.append(random.choice(string.ascii_lowercase))
                            else:
                                new_pattern.append(char)
                        new_pattern_str = ''.join(new_pattern)

                    # Create new pattern node
                    new_pattern_node = sqlglot.expressions.Literal(this=new_pattern_str, is_string=True)
                    node.args['pattern'] = new_pattern_node
                except Exception as e:
                    pass  # Failed to modify LIKE expression pattern
        elif mutation_type == 'RdMRegExp':
            # Modify REGEXP expression pattern
            if hasattr(node, 'args') and 'pattern' in node.args:
                original_pattern = node.args['pattern']
                pattern_str = original_pattern.this if hasattr(original_pattern, 'this') else str(original_pattern)

                try:
                    # Remove possible single quotes
                    if pattern_str.startswith("'"):
                        pattern_str = pattern_str[1:]
                    if pattern_str.endswith("'"):
                        pattern_str = pattern_str[:-1]

                    if direction == 1:
                        # Expand results: Remove '^' and '$', replace '+' and '?' with '*'
                        new_pattern_str = pattern_str.replace('^', '').replace('$', '')
                        new_pattern_str = new_pattern_str.replace('+', '*').replace('?', '*')
                    else:
                        # Shrink results: Add '^' at the beginning and '$' at the end, replace '*' with '+' or '?'
                        new_pattern_str = pattern_str
                        # 50% probability to add ^ and $
                        if random.random() < 0.5 and not new_pattern_str.startswith('^'):
                            new_pattern_str = '^' + new_pattern_str
                        if random.random() < 0.5 and not new_pattern_str.endswith('$'):
                            new_pattern_str = new_pattern_str + '$'
                        # Replace '*' with '+' or '?'
                        new_pattern = []
                        for i, char in enumerate(new_pattern_str):
                            if char == '*' and (i == 0 or new_pattern_str[i-1] not in ['\\']):
                                new_pattern.append(random.choice(['+', '?']))
                            else:
                                new_pattern.append(char)
                        new_pattern_str = ''.join(new_pattern)

                    # Create new pattern node
                    new_pattern_node = sqlglot.expressions.Literal(this=new_pattern_str, is_string=True)
                    node.args['pattern'] = new_pattern_node
                except Exception as e:
                    pass  # Failed to modify REGEXP expression pattern
        elif mutation_type == 'FixMLogicalAnd':
            # Logical AND expression mutation: a AND b AND c -> a AND b (remove the last condition)
            if hasattr(node, 'args') and 'expressions' in node.args:
                expressions = node.args['expressions']
                if expressions and len(expressions) > 1:
                    # Remove the last expression, keep the previous ones
                    new_expressions = expressions[:-1]
                    node.args['expressions'] = new_expressions
                    item['direction'] = 1  # Expand results: reducing conditions gives more results
        elif mutation_type == 'FixMInnerJoin':
            # INNER JOIN -> LEFT JOIN mutation
            if hasattr(node, 'args') and 'kind' in node.args:
                # Change INNER JOIN to LEFT JOIN
                node.args['kind'] = 'LEFT'
                item['direction'] = 1  # Expand results: LEFT JOIN gives more results
        elif mutation_type == 'FixMIntersect':
            # INTERSECT -> UNION mutation
            # Create new UNION node to replace INTERSECT node
            try:
                # Get left and right expressions of INTERSECT node
                left_expr = None
                right_expr = None
                if hasattr(node, 'args'):
                    if 'this' in node.args:
                        left_expr = node.args['this']
                    if 'expression' in node.args:
                        right_expr = node.args['expression']
                
                if left_expr and right_expr:
                    # Create UNION node, use DISTINCT by default
                    union_node = sqlglot.expressions.Union(
                        this=left_expr,
                        expression=right_expr,
                        distinct=True
                    )
                    # Replace current node
                    node.replace(union_node)
                    item['direction'] = 1  # Expand results: INTERSECT -> UNION gives more results
            except Exception as e:
                pass
        elif mutation_type == 'FixMExcept':
            # A EXCEPT B -> A mutation (remove EXCEPT operation, keep only left expression)
            # Get left expression of EXCEPT node
            left_expr = None
            if hasattr(node, 'args') and 'this' in node.args:
                left_expr = node.args['this']
            
            if left_expr:
                try:
                    # Replace the entire EXCEPT node with left expression
                    node.replace(left_expr)
                    item['direction'] = 1  # Expand results: removing EXCEPT operation gives more results
                except Exception as e:
                    pass  # Mutation failed
            else:
                pass  # Cannot get left expression
        final_direction=(item['direction']^item['flag'])^1
        return self.ast


    def get_mutated_ast(self):
        """Get the mutated AST"""
        if not self.mutated:
            return self.ast
        return self.ast

    def get_mutable_nodes_count(self):
        """Get count of mutable nodes"""
        return len(self.mutable_nodes)