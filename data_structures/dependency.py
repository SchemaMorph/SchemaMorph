# Dependency class definition - stores dependencies between nodes
from dataclasses import dataclass

@dataclass
class Dependency:
    """Dependencies between nodes"""
    source_node_id: str
    target_node_id: str
    reason: str  # description of dependency reason