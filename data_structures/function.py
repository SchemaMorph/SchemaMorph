# Function class definition - store function information
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Function:
    """Function information"""
    name: str
    min_params: int
    max_params: int  # None indicates variable parameters
    param_types: List[str]  # parameter types
    return_type: str  # return type
    func_type: str  # scalar, aggregate, window
    format_string_required: bool = False