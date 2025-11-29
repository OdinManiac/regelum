from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class Presence(Enum):
    BOTTOM = "⊥"
    ABSENT = "∅"
    PRESENT = "✓"


@dataclass(frozen=True)
class V3(Generic[T]):
    presence: Presence
    value: Optional[T] = None
    
    @classmethod
    def bottom(cls) -> V3[T]:
        return cls(Presence.BOTTOM, None)
    
    @classmethod
    def absent(cls) -> V3[T]:
        return cls(Presence.ABSENT, None)
    
    @classmethod
    def present(cls, val: T) -> V3[T]:
        return cls(Presence.PRESENT, val)
    
    @property
    def known(self) -> bool:
        return self.presence == Presence.PRESENT
    
    def is_bottom(self) -> bool:
        return self.presence == Presence.BOTTOM

