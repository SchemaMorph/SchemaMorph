# NodeType enum definition - AST node types
from enum import Enum

class NodeType(Enum):
    """AST node types"""
    COLUMN_REFERENCE = "column_reference"
    FUNCTION_CALL = "function_call"
    COMPARISON = "comparison"
    ARITHMETIC = "arithmetic"
    LOGICAL = "logical"
    CASE = "case"
    LITERAL = "literal"  # Only used when necessary, e.g., LIMIT
    SUBQUERY = "subquery"
    SELECT = "select"
    FROM = "from"
    WHERE = "where"
    GROUP_BY = "group_by"
    HAVING = "having"
    ORDER_BY = "order_by"
    LIMIT = "limit"
    SET_OPERATION = "set_operation"
    WITH = "with"