import ast
import os
import sqlglot
import copy
import random
import string
from get_seedQuery import SeedQueryGenerator
from generateAST import Change
from mutator.set_mutator import SetMutator
from mutator.value_mutator import ValueMutator
from data_structures.db_dialect import get_current_dialect



class MutateSolve:
    def __init__(self, file_path="./generated_sql/seedQuery.sql", extension=False):
        self.change = Change()
        self.file_path = file_path
        self.extension = extension
        # Get currently set database dialect
        dialect = get_current_dialect()
        self.db_type = dialect.name if dialect else 'UNKNOWN'
        # No longer load all queries during initialization, use iterator to load on demand

    def seed_query_iterator(self):
        """Use generator to read seed query file line by line
        
        Returns:
        - Generator: Yield seed queries one by one (consistent with original get_queries method)
        """
        try:
            # Get absolute path
            abs_path = os.path.abspath(self.file_path)

            with open(abs_path, 'r', encoding='utf-8') as f:
                # Read file line by line
                for line in f:
                    # Remove whitespace characters from the beginning and end of the line
                    sql = line.strip()
                    # Ignore empty lines
                    if sql:
                        yield sql
        except Exception as e:
            pass

    def mutate_main(self, batch_size=100, max_queries=None):
        """Process seed queries (batch processing to reduce memory usage)
        
        Parameters:
        - batch_size: Number of queries to process per batch
        - max_queries: Maximum number of queries to process (None means process all)
        - aggregate_mutation_type: Aggregate function mutation type (None, 'normal', 'math_equivalence')
        """
        executor = SeedQueryGenerator()
        
        # Count processed queries
        processed_count = 0
        batch_count = 0
        
        # Process queries one by one using iterator
        for query in self.seed_query_iterator():
            # Check if maximum processing count is reached
            if max_queries is not None and processed_count >= max_queries:
                break
            
            try:
                # Print progress
                processed_count += 1
                print(f"Current query {processed_count}")
                # Parse query into AST
                ast = self.change.ASTChange(query)
                sql = str(ast)
                if not self.extension:
                    # Preprocess AST
                    ast = self.detailsolve(ast)
                    # Create ChangeAST object and process, passing dialect information
                    change_ast = SetMutator(ast, db_type=self.db_type)
                    # Identify mutable nodes
                    mutable_nodes = change_ast.findnode(ast)
                    # Execute mutation
                    change_ast.mutate(executor)
                else:
                    mutator = ValueMutator(ast)
                    # Find aggregate function nodes
                    mutator.find_aggregate_nodes(ast)
                    # Execute mutation
                    mutator.mutate()
                # Execute query
                result_data = executor.execute_query(sql)
                # Skip only when query execution fails (returns None), continue mutation when result is empty ([])
                if result_data is None:
                    continue
                
                # Release some memory after processing a batch of queries
                if processed_count % batch_size == 0:
                    batch_count += 1
                    print(f"Completed batch {batch_count}, releasing some memory...")
                    # Explicitly delete large objects to help garbage collection
                    del ast, sql
                    # Only delete these variables if they are defined
                    if 'change_ast' in locals():
                        del change_ast
                    if 'mutable_nodes' in locals():
                        del mutable_nodes
                    
            except Exception as e:
                # Continue processing next query
                continue
        
        print(f"\nMutation completed! Processed a total of {processed_count} seed queries")

    def _is_aggregate_function(self, node):
        """Check if a node is an aggregate function"""
        # List of aggregate function types, based on sqlglot's supported class names
        aggregate_functions = [
            'Avg', 'Count', 'Max', 'Min', 'Sum',
            'GroupConcat',
            'Std', 'Stddev', 'StddevPop', 'StddevSamp',
            'Variance', 'VariancePop', 'VarPop', 'VarSamp', 'StdDevPop', 'StdDevSamp',
            'BitAnd', 'BitOr', 'BitXor','Exp',
            
        ]

        # Check if node type name is an aggregate function
        if hasattr(node, '__class__'):
            class_name = node.__class__.__name__
            
            # Directly check class name
            if class_name in aggregate_functions:
                return True
            
            # Special handling for Anonymous type aggregate functions
            if class_name == 'Anonymous':
                # Check if function name is an aggregate function
                if hasattr(node, 'name') and node.name is not None:
                    func_name = node.name.upper()
                    if func_name in [
                        'STD', 'STDDEV', 'VAR', 'VARIANCE', 'BIT_AND', 'BIT_OR', 'BIT_XOR',
                        'AVG', 'COUNT', 'MAX', 'MIN', 'SUM', 'GROUP_CONCAT','EXP',
                    ]:
                        return True
                elif hasattr(node, 'this') and isinstance(node.this, str):
                    # In some cases, this attribute is a function name in string form
                    func_name = node.this.upper()
                    if func_name in [
                        'STD', 'STDDEV', 'VAR', 'VARIANCE', 'BIT_AND', 'BIT_OR', 'BIT_XOR',
                        'AVG', 'COUNT', 'MAX', 'MIN', 'SUM', 'GROUP_CONCAT','EXP',
                    ]:
                        return True
        
        return False
    
    def detailsolve(self, ast):
        """
        Preprocess SQL AST to support various SQL structures, including set operations
        Refactoring: First get all select-type nodes through walk, then process them one by one
        """
        try:
            # Step 2: Get all select type nodes
            select_nodes = []
            for node in ast.walk():
                if isinstance(node, sqlglot.expressions.Select):
                    select_nodes.append(node)
            
            
            # Step 3: Process SELECT nodes one by one
            for i, select_node in enumerate(select_nodes):
                self._process_select_node(select_node)
        except Exception as e:
            pass
        
        return ast
      

    def _process_select_node(self, ast):
        """Process a single SELECT node"""
        
        # Process SELECT clause
        for i, expr in enumerate(ast.expressions):
            # Process window function
            if isinstance(expr, sqlglot.expressions.Alias) and isinstance(expr.this, sqlglot.expressions.Window):
                # Replace window function with integer value 1
                new_expr = sqlglot.expressions.Alias(
                    this=sqlglot.expressions.Literal(this=1, is_string=False),
                    alias=expr.alias
                )
                ast.expressions[i].replace(new_expr)
            # Process aggregate function
            elif isinstance(expr, sqlglot.expressions.Alias) and self._is_aggregate_function(expr.this):
                agg_func = expr.this
                
                # Get aggregate function arguments
                args = []
                if agg_func.__class__.__name__ == 'GroupConcat':
                    args.append(agg_func.this.this)
                elif agg_func.__class__.__name__ != 'Anonymous' and hasattr(agg_func, 'args') and 'this' in agg_func.args:
                    args = agg_func.args['this'] if isinstance(agg_func.args['this'], list) else [agg_func.args['this']]
                elif agg_func.__class__.__name__ =='Anonymous':
                    args = agg_func.expressions
                # Check if it contains distinct
                has_distinct = False
                
                # In sqlglot, DISTINCT is represented by including a Distinct node in the this attribute of the aggregate function
                if hasattr(agg_func, 'this') and hasattr(agg_func.this, '__class__') and agg_func.this.__class__.__name__ == 'Distinct':
                    has_distinct = True
                    # If it's DISTINCT, the arguments are in the expressions of the Distinct node
                    if hasattr(agg_func.this, 'expressions') and agg_func.this.expressions:
                        args = agg_func.this.expressions
                
                # If there are arguments, replace with the first argument
                if args:
                    # Preserve original alias
                    new_expr = sqlglot.expressions.Alias(
                        this=args[0],
                        alias=expr.alias
                    )
                    ast.expressions[i].replace(new_expr)
                else:
                    pass
            elif isinstance(expr, sqlglot.expressions.Alias) and isinstance(expr.this, sqlglot.expressions.Subquery):
                new_expr = sqlglot.expressions.Alias(
                    this=sqlglot.expressions.Literal(this=1, is_string=False),
                    alias=expr.alias
                )
                ast.expressions[i].replace(new_expr)

        # Process limit clause
        # Get and remove LIMIT clause through args dictionary
        if hasattr(ast, 'args') and 'limit' in ast.args and ast.args['limit']:
            limit_clause = ast.args['limit']
            # Delete LIMIT clause
            del ast.args['limit']

        
        # Process join clause
        if hasattr(ast, 'args') and 'joins' in ast.args:
            
            joins_clause = ast.args['joins']
            if joins_clause:
                # Only process direct JOIN nodes under the current JOIN clause, not deeper JOINs
                # Check if JOIN clause has expressions attribute
                for clause in joins_clause:
                    if 'side' in clause.args:
                        clause.args['side'] = None
                        # Check if it's already INNER JOIN (access join_type through args dictionary)
                        clause.args['kind'] = 'INNER'
                    if 'kind' in clause.args:
                        clause.args['kind'] = 'INNER'
        
        # Process group clause 
        if hasattr(ast, 'args') and 'group' in ast.args:
            group_clause = ast.args['group']
            if group_clause:
                # Delete GROUP BY clause
                del ast.args['group']
        # Process HAVING clause
        if hasattr(ast, 'args') and 'having' in ast.args and ast.args['having']:
            having_clause = ast.args['having']
            
            # Check if the internal child nodes of HAVING clause contain aggregate functions
            for value in having_clause.walk():
                    if isinstance(value, sqlglot.expressions.Expression) and self._is_aggregate_function(value):
                        # Found aggregate function child node
                        agg_func = value
                        # Get aggregate function arguments
                        args = []
                        if agg_func.__class__.__name__ == 'GroupConcat':
                            args.append(agg_func.this.this)
                        elif agg_func.__class__.__name__ != 'Anonymous' and hasattr(agg_func, 'args') and 'this' in agg_func.args:
                            args = agg_func.args['this'] if isinstance(agg_func.args['this'], list) else [agg_func.args['this']]
                        elif agg_func.__class__.__name__ =='Anonymous':
                            args = agg_func.expressions
                        # Check if it contains distinct
                        has_distinct = False
                        
                        # In sqlglot, DISTINCT is represented by including a Distinct node in the this attribute of the aggregate function
                        if hasattr(agg_func, 'this') and hasattr(agg_func.this, '__class__') and agg_func.this.__class__.__name__ == 'Distinct':
                            has_distinct = True
                            # If it's DISTINCT, the arguments are in the expressions of the Distinct node
                            if hasattr(agg_func.this, 'expressions') and agg_func.this.expressions:
                                args = agg_func.this.expressions
                        
                        # If there are arguments, replace with the first argument
                        if args:
                            # Preserve original alias
                            new_expr = args[0]
                            agg_func.replace(new_expr)

        if hasattr(ast, 'args') and 'where' in ast.args and ast.args['where']:
            where_clause = ast.args['where']
            
            # Use walk method to iterate through all nodes and directly process aggregate functions
            # Convert walk results to list to avoid modification issues during iteration
            for node in list(where_clause.walk()):
                if self._is_aggregate_function(node):
                    
                    # Get aggregate function arguments
                    replacement = None
                    if hasattr(node, 'args') and 'this' in node.args:
                        args = node.args['this']
                        replacement = args[0] if isinstance(args, list) and args else args
                    elif hasattr(node, 'this') and node.this:
                        # Handle Distinct case
                        if hasattr(node.this, '__class__') and node.this.__class__.__name__ == 'Distinct':
                            if hasattr(node.this, 'expressions') and node.this.expressions:
                                replacement = node.this.expressions[0] if node.this.expressions else None
                        else:
                            replacement = node.this
                    
                    # If there is a replacement parameter, replace the node directly
                    if replacement:
                        # Get the parent node and key name of the node, then perform replacement
                        parent = None
                        parent_key = None
                        # Find parent node and key name
                        for potential_parent in list(where_clause.walk()):
                            if hasattr(potential_parent, 'args'):
                                for key, value in potential_parent.args.items():
                                    if value is node:
                                        parent = potential_parent
                                        parent_key = key
                                        break
                                    elif isinstance(value, list):
                                        for i, item in enumerate(value):
                                            if item is node:
                                                parent = potential_parent
                                                parent_key = (key, i)
                                                break
                                if parent:
                                    break
                        
                        # Execute replacement
                        if parent and parent_key:
                            if isinstance(parent_key, tuple):
                                # Replace element in list
                                parent.args[parent_key[0]][parent_key[1]] = replacement
                            else:
                                # Replace value in dictionary
                                parent.args[parent_key] = replacement
                    
        # Process scalar query
        # Determine if it's a scalar query (a query that returns a single row and column result)
        # Simple judgment here: only one expression, no GROUP BY clause, no JOIN, might be a scalar query
        is_scalar_query = False
        if hasattr(ast, 'expressions') and len(ast.expressions) == 1:
            # Check if there's no GROUP BY clause
            has_group_by = hasattr(ast, 'args') and 'group' in ast.args and ast.args['group']
            
            # Check if there's no complex JOIN
            has_complex_join = False
            if hasattr(ast, 'args') and 'from' in ast.args and ast.args['from']:
                from_clause = ast.args['from']
                if hasattr(from_clause, 'expressions'):
                    for expr in from_clause.expressions:
                        if isinstance(expr, sqlglot.expressions.Join):
                            has_complex_join = True
                            break
            
            # Judged as scalar query
            if not has_group_by and not has_complex_join:
                is_scalar_query = True
        
        if is_scalar_query:
            order_by_expr = None
            # Get column from SELECT expression as ORDER BY parameter
            select_expr = ast.expressions[0]
            if isinstance(select_expr, sqlglot.expressions.Alias) and isinstance(select_expr.this, sqlglot.expressions.Column):
                order_by_expr = select_expr.this
            elif isinstance(select_expr, sqlglot.expressions.Column):
                order_by_expr = select_expr
            
            # Add ORDER BY clause
            if hasattr(ast, 'args'):
                if order_by_expr:
                    # Create ORDER BY expression
                    order_by = sqlglot.expressions.Order(expressions=[order_by_expr])
                    ast.args['order'] = order_by
                
                # Add LIMIT 1 clause
                limit = sqlglot.expressions.Limit(expression='1')
                ast.args['limit'] = limit

        
        
        
        
    
    
