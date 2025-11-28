from enum import Enum
from typing import Generic, TypeVar, Optional
from dataclasses import dataclass

T = TypeVar("T")

class B3(Enum):
    BOTTOM = "⊥"
    FALSE = "0"
    TRUE = "1"

@dataclass
class V3(Generic[T]):
    value: Optional[T]
    known: bool
    
    @classmethod
    def bottom(cls):
        return cls(None, False)
    
    @classmethod
    def known(cls, val: T):
        return cls(val, True)
    
    def is_bottom(self) -> bool:
        return not self.known

