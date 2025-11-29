from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Any, Dict, Optional
from dataclasses import dataclass
from .types import NodeId

T = TypeVar("T")

@dataclass
class Intent(Generic[T]):
    variable: "Variable[T]"
    producer: NodeId
    value: T

class WritePolicy(ABC, Generic[T]):
    @abstractmethod
    def merge(self, intents: List[Intent[T]]) -> T:
        """Merge multiple intents into one value."""
        ...
    
    def is_monotone(self) -> bool:
        return False
    
    def height_bound(self) -> Optional[int]:
        return None
    
    def allows_multiwriter(self) -> bool:
        return False

class ErrorPolicy(WritePolicy[T]):
    def merge(self, intents: List[Intent[T]]) -> T:
        if len(intents) > 1:
            producers = [i.producer for i in intents]
            raise ValueError(f"Multiple writes detected for ErrorPolicy: {producers}")
        if not intents:
            raise ValueError("No values to merge")
        return intents[0].value
    
    def is_monotone(self) -> bool:
        return True
    
    def height_bound(self) -> Optional[int]:
        return 1

class SumPolicy(WritePolicy[Any]):
    def merge(self, intents: List[Intent[Any]]) -> Any:
        if not intents:
            return 0
        return sum(i.value for i in intents)
    
    def is_monotone(self) -> bool:
        return True
    
    def allows_multiwriter(self) -> bool:
        return True

class LWWPolicy(WritePolicy[T]):
    def __init__(self, priority_order: List[NodeId]):
        self.priority_map = {nid: i for i, nid in enumerate(priority_order)}

    def merge(self, intents: List[Intent[T]]) -> T:
        if not intents:
            raise ValueError("No values to merge")
            
        def get_prio(intent: Intent[T]) -> int:
            return self.priority_map.get(intent.producer, -1)
            
        best = max(intents, key=get_prio)
        return best.value
    
    def allows_multiwriter(self) -> bool:
        return False

class Variable(Generic[T]):
    def __init__(self, name: str, init: T, write_policy: WritePolicy[T], *, is_delay_buffer: bool = False):
        self.name = name
        self.init = init
        self.write_policy = write_policy
        self.is_delay_buffer = is_delay_buffer
        
    def __repr__(self):
        return f"Variable({self.name}, init={self.init})"
