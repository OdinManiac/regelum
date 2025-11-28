# Remove duplicate Tag definition and use the one in time.py
# But types.py is imported by many files.
# Let's re-export Tag from types.py but import from time.py to have single definition.
# Or move definition to types.py fully. 
# Tag in time.py has logic methods.
# Let's keep Tag in core/time.py and import it in types.py if needed, 
# or just fix imports.

# Check usage
# grep -r "Tag" src/rg_compiler

# src/rg_compiler/core/time.py: class Tag
# src/rg_compiler/core/types.py: class Tag(NamedTuple)

# Let's delete Tag from types.py and use time.py everywhere.
from typing import NewType, NamedTuple

NodeId = NewType("NodeId", str)
PortId = NewType("PortId", str)

# Removed Tag definition to avoid duplication with core/time.py
