from typing import Any

class AbsentType:
    def __repr__(self): return "ABSENT"
    def __bool__(self): return False # Absent is falsy? Or should be careful?

ABSENT = AbsentType()

def is_absent(val: Any) -> bool:
    return val is ABSENT

def is_present(val: Any) -> bool:
    return val is not ABSENT and val is not None # Treating None as Absent too?

