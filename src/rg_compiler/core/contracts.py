from typing import Any, Optional
from dataclasses import dataclass

@dataclass
class Contract:
    deterministic: bool = True
    no_side_effects: bool = True
    monotone: bool = False
    no_instant_loop: bool = True
    max_latency_ms: Optional[int] = None

def contract(
    deterministic: bool = True,
    no_side_effects: bool = True,
    monotone: bool = False,
    no_instant_loop: bool = True,
    max_latency_ms: Optional[int] = None,
):
    def decorator(func):
        func._contract = Contract(
            deterministic=deterministic,
            no_side_effects=no_side_effects,
            monotone=monotone,
            no_instant_loop=no_instant_loop,
            max_latency_ms=max_latency_ms,
        )
        return func
    return decorator

def unsafe(reason: str):
    def decorator(func):
        func._unsafe = True
        func._unsafe_reason = reason
        # Unsafe implies no guarantees
        func._contract = Contract(
            deterministic=False,
            no_side_effects=False,
            monotone=False,
            no_instant_loop=False,
        )
        return func
    return decorator

