from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, get_type_hints, Generic, TypeVar, Optional
from .node import RawNode, Context, IntentContext
from .dsl import Expr, Var
from .variables import Variable, WritePolicy, LWWPolicy
from .interpreter import eval_expr
from .values import is_absent, ABSENT

T = TypeVar("T")

class State(Generic[T]):
    def __init__(self, init: T, policy: Optional[WritePolicy[T]] = None):
        self.init = init
        self.policy = policy or LWWPolicy(priority_order=[]) 
        self.name: Optional[str] = None
        
    def __set_name__(self, owner, name):
        self.name = name

    def set(self, expr: Any):
        pass
        
    def __get__(self, instance, owner):
        if instance is None:
            return self
        return BoundState(self, instance)

class BoundState(Generic[T]):
    def __init__(self, state_descr: State[T], instance: 'CoreNode'):
        self.descr = state_descr
        self.instance = instance
        
    def set(self, expr: Any):
        if self.descr.name:
            self.instance._register_state_write(self.descr.name, expr)

class Input(Generic[T]):
    def __init__(self, rate: Optional[int] = None, default: Any = None):
        self.rate = rate
        self.default = default

class Output(Generic[T]):
    def __init__(self, rate: Optional[int] = None):
        self.rate = rate

@dataclass
class CoreReaction:
    name: str
    ast: Expr[Any]
    input_names: List[str]
    output_name: Optional[str]
    writes: Dict[str, Expr[Any]] = field(default_factory=dict) 

def reaction(func):
    func._is_reaction = True
    return func

class CoreNode(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.reactions: List[CoreReaction] = []
        self._state_vars: Dict[str, Variable[Any]] = {}
        self._current_writes: Dict[str, Expr[Any]] = {} 
        self._build_structure()
        self._compile_reactions()

    def _build_structure(self):
        for name, attr in self.__class__.__dict__.items():
            if isinstance(attr, Input):
                port = self.add_input(name, default=attr.default)
                port.rate = attr.rate 
            elif isinstance(attr, Output):
                port = self.add_output(name)
                port.rate = attr.rate
            elif isinstance(attr, State):
                var_name = f"{self.id}.{name}"
                var = Variable(var_name, attr.init, attr.policy)
                self._state_vars[name] = var

    def _register_state_write(self, name: str, expr: Any):
        if not isinstance(expr, Expr):
            from .dsl import Const
            expr = Const(expr)
        self._current_writes[name] = expr

    def _compile_reactions(self):
        for name in dir(self):
            method = getattr(self, name)
            if getattr(method, "_is_reaction", False):
                self._compile_single_reaction(name, method)

    def _compile_single_reaction(self, name: str, method: Callable):
        self._current_writes = {} 
        
        hints = get_type_hints(method)
        args = []
        arg_names = []
        code = method.__code__
        func_args = code.co_varnames[1:code.co_argcount]
        
        for arg_name in func_args:
            arg_names.append(arg_name)
            args.append(Var(arg_name))
            
        ast = method(*args)
        
        output_name = "out" if "out" in self.outputs else None
        
        self.reactions.append(CoreReaction(
            name=name, 
            ast=ast, 
            input_names=arg_names, 
            output_name=output_name,
            writes=self._current_writes.copy()
        )) 
        self._current_writes = {}

    def step(self, ctx: Context) -> None:
        for r in self.reactions:
            env = {}
            for arg in r.input_names:
                if arg in self.inputs:
                    val = ctx.read(self.inputs[arg])
                    # Note: ctx.read uses port.default if ABSENT? 
                    # We need to update RuntimeIntentContext.read to use default.
                    # But if Runtime handles it, val is already resolved.
                    # If still ABSENT, and no default...
                    
                    # In CoreNode logic, we often need a scalar.
                    # If val is ABSENT, what do we put in env?
                    # The interpreter handles ABSENT propagation.
                    # But math ops might fail if types are strict.
                    
                    env[arg] = val
                elif arg in self._state_vars:
                    var = self._state_vars[arg]
                    if isinstance(ctx, IntentContext):
                        env[arg] = ctx.read_var(var)
                    else:
                        env[arg] = var.init
                else:
                    env[arg] = ABSENT # Fallback
            
            result = eval_expr(r.ast, env)
            
            if r.output_name and r.output_name in self.outputs:
                ctx.write(self.outputs[r.output_name], result)
                
            if isinstance(ctx, IntentContext):
                for state_name, expr in r.writes.items():
                    val = eval_expr(expr, env)
                    var = self._state_vars[state_name]
                    ctx.write_var(var, val)
