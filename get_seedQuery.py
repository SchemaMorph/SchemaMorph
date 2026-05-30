import os
import pymysql
import psycopg2
from data_structures.db_dialect import get_current_dialect
import os

class SeedQueryGenerator:
    def __init__(self, file_path='generated_sql/queries.sql', db_config=None):
        self.file_path = file_path
        # Database configuration default values
        self.db_config = db_config or {
            'host': '127.0.0.1',
            'user': 'root',
            'password': '123456',
            'database': 'test',
            'port': 13306  # Default MySQL port
        }
        # No longer read all queries during initialization, instead read on demand

    def query_iterator(self):
        """Use generator to read SQL query file line by line, reducing memory usage
        
        Returns:
        - generator: Returns SQL queries one by one (consistent with original get_queries method)
        """
        try:
            # Get absolute path
            abs_path = os.path.abspath(self.file_path)

            with open(abs_path, 'r', encoding='utf-8') as f:
                # Read file line by line
                for line in f:
                    # Remove whitespace characters from beginning and end of line
                    sql = line.strip()
                    # Ignore empty lines
                    if sql:
                        yield sql
        except Exception as e:
            pass

    def get_queries_count(self):
        """Get the total number of SQL queries without loading all queries into memory
        
        Returns:
        - int: Total number of queries
        """
        count = 0
        for _ in self.query_iterator():
            count += 1
        return count

    def connect_db(self):
        """Connect to the database corresponding to the currently set database dialect
        
        Returns:
        - conn: Database connection object
        """
        try:
            # Get current database dialect
            dialect = get_current_dialect()
            dialect_name = dialect.name.upper()
            # Create corresponding database connection based on dialect type
            if dialect_name in ["MYSQL", "MARIADB", "TIDB", "OCEANBASE","PERCONA", "POLARDB"]:
                # MySQL series database configuration
                if dialect_name == "MYSQL":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'sqlancer',
                        'password': 'sqlancer',
                        'database': 'test',
                        'port': 3307
                    })
                elif dialect_name == "MARIADB":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'sqlancer',
                        'password': 'sqlancer',
                        'database': 'test',
                        'port': 3308
                    })
                elif dialect_name == "TIDB":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'root',
                        'password': '123456',
                        'database': 'test',
                        'port': 4000
                    })
                elif dialect_name == "OCEANBASE":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'root',
                        'password': '',
                        'database': 'test',
                        'port': 2881
                    })
                elif dialect_name == "PERCONA":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'root',
                        'password': '123456',
                        'database': 'test',
                        'port': 23306
                    })
                elif dialect_name == "POLARDB":
                    self.db_config.update({
                        'host': '127.0.0.1',
                        'user': 'polardbx_root',
                        'password': '123456',
                        'database': 'test',
                        'port': 8527
                    })
                
                # Create MySQL/MariaDB/TiDB/OceanBase connection
                connection_params = {
                    'host': self.db_config['host'],
                    'user': self.db_config['user'],
                    'password': self.db_config['password'],
                    'database': self.db_config['database'],
                    'port': self.db_config['port'],
                    'charset': 'utf8mb4'
                }
                
                # POLARDB connection uses standard parameters, no special binary processing added
                
                conn = pymysql.connect(**connection_params)
            
            else:
                raise ValueError(f"Unsupported database dialect: {dialect_name}")
            
            return conn
        except Exception as e:
            # Database connection failed
            return None

    def execute_query(self, query):
        """Execute SQL query and return results
        
        Parameters:
        - query: SQL query statement
        
        Returns:
        - For SELECT statements: Returns a tuple containing two elements (result set, column name list)
        - For other statements: Returns the number of affected rows
        - If query fails: Returns None
        """
        # Check if query is empty
        if not query or query.strip() == '':
            return None
        # Get current database dialect
        dialect = get_current_dialect()
        dialect_name = dialect.name.upper()
        conn = self.connect_db()
        if conn is None:
            return None
        try:
            with conn.cursor() as cursor:
                # For PostgreSQL dialect, process TO_CHAR function calls, add explicit type conversion
                if dialect_name == "POSTGRESQL":
                    # Match TO_CHAR('date string', 'format') pattern, add ::DATE type conversion
                    import re
                    # Regular expression to match TO_CHAR('date string', 'format') pattern
                    pattern = r"TO_CHAR\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)"
                    # Use replacement function to add type conversion
                    def add_type_cast(match):
                        date_str = match.group(1)
                        format_str = match.group(2)
                        return f"TO_CHAR('{date_str}'::DATE, '{format_str}')"
                    # Execute replacement
                    query = re.sub(pattern, add_type_cast, query)
                # For MySQL dialect, apply function name mapping
                elif dialect_name == "MYSQL":
                    import re
                    # Create MySQLDialect instance to use get_function_name method
                    from data_structures.db_dialect import MySQLDialect
                    mysql_dialect = MySQLDialect()
                    # Match all uppercase function names (may contain underscores)
                    function_pattern = r"\b([A-Z][A-Z_]+)\s*\("
                    # Use replacement function to apply mapping
                    def apply_function_mapping(match):
                        function_name = match.group(1)
                        mapped_name = mysql_dialect.get_function_name(function_name)
                        return f"{mapped_name}("
                    # Execute replacement
                    query = re.sub(function_pattern, apply_function_mapping, query)
                    
                cursor.execute(query)
                # Check if it's a SELECT query or WITH type query
                # Improved logic: Skip leading whitespace and parentheses to handle queries with set operations
                processed_query = query.strip().upper()
                # Skip leading parentheses
                while processed_query.startswith('('):
                    processed_query = processed_query[1:].strip()
                
                # Identify both SELECT and WITH type queries
                is_select_query = processed_query.startswith('SELECT') or processed_query.startswith('WITH')
                
                if is_select_query:
                    # Get results
                    result = cursor.fetchall()
                    # Get column names
                    column_names = [desc[0] for desc in cursor.description] if cursor.description else []
                    # Return result set and column names
                    return (result, column_names)
                else:
                    conn.commit()
                    return cursor.rowcount  # Return number of affected rows
        except Exception as e:
            pass
            return None
        finally:
            # Ensure connection is closed
            if conn:
                conn.close()

    def execute_queries(self):
        """Execute all SQL queries (use generator to reduce memory usage)
        """
        for query in self.query_iterator():
            self.execute_query(query)

    def get_seedQuery(self, batch_size=500):
        """Get seed queries (process in batches to reduce memory usage)
        
        Parameters:
        - batch_size: Number of queries to process per batch
        """
        # Get current database dialect
        dialect = get_current_dialect()
        dialect_name = dialect.name.upper()
        
        # Pre-create file and write database-specific use statements
        seed_file_path = "./generated_sql/seedQuery.sql"
        with open(seed_file_path, "w", encoding="utf-8") as f:
            if dialect_name == "MYSQL":
                f.write("USE test;\n")
            elif dialect_name == "POSTGRESQL":
                f.write("-- PostgreSQL doesn't have USE statements, database connection is specified through connection parameters\n")
            else:
                f.write(f"-- Current dialect: {dialect_name}\n")

        # Get total number of queries
        total_queries = self.get_queries_count()
        print(f"Found a total of {total_queries} SQL queries:")

        # Count successful SELECT queries
        success_count = 0
        
        # Process queries in batches
        batch = []
        batch_count = 0
        
        # Process queries one by one using iterator
        for i, sql in enumerate(self.query_iterator(), 1):
            if i % 1000 == 0:  # Print progress every 1000 queries processed
                print(f"Processed {i}/{total_queries} queries")
            
            print(f"Testing query {i}:")
            # Execute query and get results
            result = self.execute_query(sql)
            
            # Process results
            if result is not None:
                # Check if result is an integer (non-SELECT query case)
                if isinstance(result, int):
                    pass
                else:
                    # SELECT query, add to batch regardless of whether results are empty or not
                    batch.append(sql)
                    batch.append("\n")
                    success_count += 1
            
            # When batch reaches specified size, write to file and clear batch
            if len(batch) >= batch_size * 2:  # Each query has a newline, so multiply by 2
                with open(seed_file_path, "a", encoding="utf-8") as f:
                    for seed in batch:
                        f.write(seed)
                batch = []  # Clear batch to free memory
                batch_count += 1
        
        # Process the last batch
        if batch:
            with open(seed_file_path, "a", encoding="utf-8") as f:
                for seed in batch:
                    f.write(seed)
        
        print(f"\nSeed query generation completed! Successfully extracted {success_count} valid SELECT queries")
        print(f"Seed queries saved to: {seed_file_path}")


    
       