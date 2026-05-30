# Table class definition - store table information
from dataclasses import dataclass
from typing import List, Dict, Optional
import random
from .column import Column

@dataclass
class Table:
    """Table information"""
    name: str
    columns: List[Column]
    primary_key: str
    foreign_keys: List[Dict]  # Foreign key info: {column, ref_table, ref_column}
    indexes: List[Dict] = None  # Index info: {name, columns, is_primary=False}

    def has_column(self, column_name: str) -> bool:
        """Check if the table contains the specified column"""
        return any(col.name == column_name for col in self.columns)

    def get_column(self, column_name: str) -> Optional[Column]:
        """Get specified column, return None if not exists"""
        for col in self.columns:
            if col.name == column_name:
                return col
        return None

    def get_similar_columns(self, column_name: str) -> List[Column]:
        """Get columns with similar names (for replacement)"""
        if len(column_name) >= 3:
            prefix = column_name[:3]
            return [col for col in self.columns if col.name.startswith(prefix)]
        return []

    def get_random_column(self, category: Optional[str] = None) -> Column:
        """Get random column, support category filtering"""
        candidates = self.columns
        if category:
            candidates = [col for col in candidates if col.category == category]
        if not candidates:
            candidates = self.columns
        return random.choice(candidates)
    
    def get_all_indexes(self) -> List[Dict]:
        """Get all indexes (including primary key index)"""
        if self.indexes is None:
            return []
        return self.indexes
    
    def get_non_primary_indexes(self) -> List[Dict]:
        """Get all non-primary key indexes"""
        if self.indexes is None:
            return []
        return [idx for idx in self.indexes if not idx.get('is_primary', False)]

    def add_index(self, index_name: str, columns: List[str], is_primary: bool = False) -> None:
        """Add index information"""
        if self.indexes is None:
            self.indexes = []
        self.indexes.append({
            'name': index_name,
            'columns': columns,
            'is_primary': is_primary
        })
    
    def has_index(self, index_name: str) -> bool:
        """Check if index with specified name exists"""
        if self.indexes is None:
            return False
        return any(idx['name'] == index_name for idx in self.indexes)