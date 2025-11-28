from typing import Generic, TypeVar, Literal, Any, Union
from dataclasses import dataclass

T = TypeVar("T")

@dataclass
class Expr(Generic[T]):
    def __add__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("+", self, other)
        
    def __radd__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("+", other, self)

    def __sub__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("-", self, other)
        
    def __rsub__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("-", other, self)

    def __mul__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("*", self, other)
        
    def __rmul__(self, other: Union["Expr[T]", T]) -> "Expr[T]":
        if not isinstance(other, Expr):
            other = Const(other)
        return BinOp("*", other, self)

    def __lt__(self, other: Union["Expr[T]", T]) -> "Expr[bool]":
        if not isinstance(other, Expr):
            other = Const(other)
        return Cmp("<", self, other)
        
    def __gt__(self, other: Union["Expr[T]", T]) -> "Expr[bool]":
        if not isinstance(other, Expr):
            other = Const(other)
        return Cmp(">", self, other)

    def __le__(self, other: Union["Expr[T]", T]) -> "Expr[bool]":
        if not isinstance(other, Expr):
            other = Const(other)
        return Cmp("<=", self, other)

    def __ge__(self, other: Union["Expr[T]", T]) -> "Expr[bool]":
        if not isinstance(other, Expr):
            other = Const(other)
        return Cmp(">=", self, other)
        
    def __eq__(self, other: Union["Expr[T]", T]) -> "Expr[bool]":
        if not isinstance(other, Expr):
            other = Const(other)
        return Cmp("==", self, other)

@dataclass
class Const(Expr[T]):
    value: T

@dataclass
class Var(Expr[T]):
    name: str

@dataclass
class If(Expr[T]):
    cond: Expr[bool]
    then_: Expr[T]
    else_: Expr[T]

@dataclass
class BinOp(Expr[T]):
    op: Literal["+", "-", "*", "/", "min", "max"]
    left: Expr[T]
    right: Expr[T]

@dataclass
class Cmp(Expr[bool]):
    op: Literal["<", "<=", "==", ">", ">="]
    left: Expr[Any]
    right: Expr[Any]

@dataclass
class Delay(Expr[T]):
    expr: Expr[T]
    default: T

def if_(cond: Expr[bool], then_: Expr[T], else_: Expr[T]) -> Expr[T]:
    return If(cond, then_, else_)

def const(val: T) -> Expr[T]:
    return Const(val)

def var(name: str) -> Expr[Any]:
    return Var(name)

def delay(expr: Expr[T], default: T) -> Expr[T]:
    return Delay(expr, default)
