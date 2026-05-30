# Column class definition - stores table column information
from dataclasses import dataclass

@dataclass
class Column:
    """Table column information"""
    name: str
    data_type: str
    category: str  # numeric, string, datetime, boolean
    is_nullable: bool
    table_name: str  # table name