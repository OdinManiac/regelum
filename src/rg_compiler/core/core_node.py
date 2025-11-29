import ast
import inspect
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, get_type_hints, Generic, TypeVar, Optional
from .node import RawNode, Context, IntentContext
from .dsl import Expr, Var, If, BinOp, Cmp, Delay
from .variables import Variable, WritePolicy, ErrorPolicy
from .interpreter import eval_expr
from .values import ABSENT

T = TypeVar("T")

class State(Generic[T]):
    def __init__(self, init: T, policy: Optional[WritePolicy[T]] = None):
        self.init = init
        self.policy = policy or ErrorPolicy()
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
    nonzeno_rank: Optional[str] = None
    nonzeno_limit: Optional[int] = None

def reaction(func=None, *, rank: Optional[str] = None, max_microsteps: Optional[int] = None):
    def decorator(f):
        f._is_reaction = True
        f._nonzeno_rank = rank
        f._nonzeno_limit = max_microsteps
        return f
    if func is not None:
        return decorator(func)
    return decorator

class CoreNode(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.reactions: List[CoreReaction] = []
        self._state_vars: Dict[str, Variable[Any]] = {}
        self._current_writes: Dict[str, Expr[Any]] = {} 
        self._delay_counter = 0
        self._build_structure()
        self._compile_reactions()
        # Ensure runtime dispatch uses CoreNode.step even if subclass named a reaction "step".
        self.step = CoreNode.step.__get__(self, CoreNode)

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
        for name, method in self._iter_reaction_methods():
            self._compile_single_reaction(name, method)

    def _iter_reaction_methods(self):
        seen = set()
        for cls in reversed(self.__class__.mro()):
            for name, attr in cls.__dict__.items():
                if getattr(attr, "_is_reaction", False) and name not in seen:
                    seen.add(name)
                    yield name, getattr(self, name)

    def _compile_single_reaction(self, name: str, method: Callable):
        hints = get_type_hints(method)
        args = []
        arg_names = []
        code = method.__code__
        func_args = code.co_varnames[1:code.co_argcount]
        
        for arg_name in func_args:
            arg_names.append(arg_name)
            args.append(Var(arg_name))

        self._lint_reaction_source(method, arg_names)
            
        self._current_writes = {}
        ast = method(*args)
        user_writes = self._current_writes.copy()
        self._current_writes = {}

        if not isinstance(ast, Expr):
            from .dsl import Const
            ast = Const(ast)

        ast = self._lower_expr(ast, name)

        for state_name, expr in user_writes.items():
            lowered_expr = self._lower_expr(expr, name)
            self._register_state_write(state_name, lowered_expr)
        
        output_name = None
        if "out" in self.outputs:
            output_name = "out"
        elif len(self.outputs) == 1:
            output_name = next(iter(self.outputs))

        rank_name = getattr(method, "_nonzeno_rank", None)
        rank_limit = getattr(method, "_nonzeno_limit", None)
        
        self.reactions.append(CoreReaction(
            name=name, 
            ast=ast, 
            input_names=arg_names, 
            output_name=output_name,
            writes=self._current_writes.copy(),
            nonzeno_rank=rank_name,
            nonzeno_limit=rank_limit,
        ))
        if output_name and isinstance(ast, Var):
            local = ast.name
            if local in self._state_vars and getattr(self._state_vars[local], "is_delay_buffer", False):
                self.outputs[output_name].is_delay_output = True
                self.outputs[output_name].delay_state_name = local
        self._current_writes = {}

    def step(self, ctx: Context) -> None:
        for r in self.reactions:
            env = {}
            for arg in r.input_names:
                if arg in self.inputs:
                    env[arg] = ctx.read(self.inputs[arg])
                elif arg in self._state_vars:
                    var = self._state_vars[arg]
                    if isinstance(ctx, IntentContext):
                        env[arg] = ctx.read_var(var)
                    else:
                        env[arg] = var.init
                else:
                    env[arg] = ABSENT # Fallback

            for state_name, var in self._state_vars.items():
                if state_name in env:
                    continue
                if isinstance(ctx, IntentContext):
                    env[state_name] = ctx.read_var(var)
                else:
                    env[state_name] = var.init
            
            result = eval_expr(r.ast, env)
            
            if r.output_name and r.output_name in self.outputs:
                ctx.write(self.outputs[r.output_name], result)
                
            if isinstance(ctx, IntentContext):
                for state_name, expr in r.writes.items():
                    val = eval_expr(expr, env)
                    var = self._state_vars[state_name]
                    ctx.write_var(var, val)

    def _lower_expr(self, expr: Expr[Any], reaction_name: str) -> Expr[Any]:
        if not isinstance(expr, Expr):
            from .dsl import Const
            return Const(expr)
        if isinstance(expr, Delay):
            state_name = self._register_delay_state(expr.default, reaction_name)
            rhs = self._lower_expr(expr.expr, reaction_name)
            self._register_state_write(state_name, rhs)
            return Var(state_name)
        if isinstance(expr, If):
            return If(
                self._lower_expr(expr.cond, reaction_name),
                self._lower_expr(expr.then_, reaction_name),
                self._lower_expr(expr.else_, reaction_name),
            )
        if isinstance(expr, BinOp):
            return BinOp(
                expr.op,
                self._lower_expr(expr.left, reaction_name),
                self._lower_expr(expr.right, reaction_name),
            )
        if isinstance(expr, Cmp):
            return Cmp(
                expr.op,
                self._lower_expr(expr.left, reaction_name),
                self._lower_expr(expr.right, reaction_name),
            )
        return expr

    def _register_delay_state(self, default: Any, reaction_name: str) -> str:
        local_name = f"__delay_{reaction_name}_{self._delay_counter}"
        self._delay_counter += 1
        if local_name not in self._state_vars:
            var_name = f"{self.id}.{local_name}"
            self._state_vars[local_name] = Variable(var_name, default, ErrorPolicy(), is_delay_buffer=True)
        return local_name

    @staticmethod
    def _expr_uses_names(node: ast.AST, names: set[str]) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in names:
                return True
        return False

    @staticmethod
    def _is_static_range(node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                for arg in node.args:
                    if not isinstance(arg, ast.Constant):
                        return False
                return True
        return False

    def _lint_reaction_source(self, method: Callable, arg_names: List[str]) -> None:
        source = inspect.getsource(method)
        dedented = textwrap.dedent(source)
        tree = ast.parse(dedented)
        target: ast.FunctionDef | None = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == method.__name__:
                target = node
                break
        if target is None:
            raise RuntimeError(f"Cannot locate AST for reaction '{method.__name__}'")

        names = set(arg_names)
        for node in ast.walk(target):
            if isinstance(node, ast.If):
                if self._expr_uses_names(node.test, names):
                    raise RuntimeError(
                        f"Python if over reactive Expr in reaction '{method.__name__}'. "
                        "Use DSL If/min/max instead."
                    )
            elif isinstance(node, ast.IfExp):
                if self._expr_uses_names(node.test, names):
                    raise RuntimeError(
                        f"Python ternary over reactive Expr in reaction '{method.__name__}'. "
                        "Use DSL If/min/max instead."
                    )
            elif isinstance(node, ast.While):
                if self._expr_uses_names(node.test, names):
                    raise RuntimeError(
                        f"Python while over reactive Expr in reaction '{method.__name__}'. "
                        "Use DSL combinators or explicit states instead."
                    )
            elif isinstance(node, ast.For):
                if self._expr_uses_names(node.iter, names):
                    raise RuntimeError(
                        f"Python for-range depending on reactive Expr in reaction '{method.__name__}'. "
                        "Use static ranges or move dynamic loops to Ext/Raw nodes."
                    )
                if not self._is_static_range(node.iter):
                    raise RuntimeError(
                        f"Only static range(...) loops are allowed in reactions ('{method.__name__}'). "
                        "Dynamic iteration should live in Ext/Raw nodes."
                    )
