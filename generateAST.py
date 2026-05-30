import os
import sqlglot
import json
from get_seedQuery import SeedQueryGenerator

class Change:
    def __init__(self,file_path="./generated_sql/seedQuery.sql"):
        self.file_path=file_path
        self.seedqueries=self.get_queries()

    def get_queries(self):
        
        """Read SQL query file line by line and return query list, skipping the first USE test; statement
        
        Parameters:
        - self.file_path: Path to the SQL query file
        
        Returns:
        - list: List containing all SQL queries
        """
        queries = []
        try:
            # Get absolute path
            abs_path = os.path.abspath(self.file_path)

            with open(abs_path, 'r', encoding='utf-8') as f:
                # Read file line by line
                for line_num, line in enumerate(f, 1):  # Start counting line numbers from 1
                    # Remove whitespace characters from beginning and end of line
                    # Filter out redundant whitespace characters (to avoid parsing errors)
                    sql = line.strip()
                    # Ignore empty lines
                    if sql:
                        queries.append(sql)
            return queries
        except Exception as e:
            print(f"Error reading SQL file: {e}")
            return []

    def getAST(self,query):
        try:
            ast = sqlglot.parse_one(query)
            return ast
        except Exception as e:
            return None
    def ASTChange(self,query):
        ast=self.getAST(query)
        return ast
       


