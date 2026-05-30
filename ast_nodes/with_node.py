# WithNode class definition - WITH clause node (CTE)
from typing import List
from .ast_node import ASTNode
from data_structures.node_type import NodeType

class WithNode(ASTNode):
    """WITH clause node (CTE)"""
    
    def __init__(self):
        super().__init__(NodeType.WITH)
        self.ctes = []  # Each element in the list is (cte_name, select_node, num_columns)
        
    def add_cte(self, name, select_node, num_columns=3):
        self.ctes.append((name, select_node, num_columns))
        self.add_child(select_node)
        
    def to_sql(self):
        if not self.ctes:
            return ''
        
        cte_parts = []
        for name, select_node, _ in self.ctes:
            cte_sql = select_node.to_sql()
            # Ensure CTE subquery is enclosed in parentheses
            if not (cte_sql.startswith('(') and cte_sql.endswith(')')):
                cte_sql = f"({cte_sql})"
            cte_parts.append(f"{name} AS {cte_sql}")
        
        return f"WITH {', '.join(cte_parts)}"
        
    def get_cte_columns(self, cte_name):
        """Get the number of columns for the specified CTE"""
        for name, _, num_columns in self.ctes:
            if name == cte_name:
                return num_columns
        return 3  # Return 3 columns by default