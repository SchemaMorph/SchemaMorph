"""mutator package, containing implementations of various SQL mutators"""
# Import ASTMutator class from ast_mutator module
from .set_mutator import SetMutator

# Import AggregateMutation class from aggregate_mutation module
from .value_mutator import ValueMutator








# Define package's public interface
__all__ = [
    'SetMutator',
    'ValueMutator'
]

# Package version information
__version__ = '1.0.1'