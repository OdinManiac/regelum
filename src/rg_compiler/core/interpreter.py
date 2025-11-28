from typing import Any, Dict
from .dsl import Expr, Const, Var, If, BinOp, Cmp, Delay
from .ternary import V3, B3
from .values import ABSENT, is_absent

def eval_expr(expr: Expr[Any], env: Dict[str, Any]) -> Any:
    if isinstance(expr, Const):
        return expr.value
    
    if isinstance(expr, Var):
        val = env.get(expr.name)
        return val if val is not None else ABSENT # Default to Absent if missing? 
        # Or env.get returns None if not found. 
        # If input is Absent, env[name] is ABSENT.
        
    if isinstance(expr, If):
        cond = eval_expr(expr.cond, env)
        if is_absent(cond):
            # Absent condition -> Absent result? Or error?
            # Strict mode: Error.
            # Pragmatic: Absent.
            return ABSENT
            
        if cond:
            return eval_expr(expr.then_, env)
        else:
            return eval_expr(expr.else_, env)
            
    if isinstance(expr, BinOp):
        l = eval_expr(expr.left, env)
        r = eval_expr(expr.right, env)
        
        if is_absent(l) or is_absent(r):
            return ABSENT
            
        op = expr.op
        if op == "+": return l + r
        if op == "-": return l - r
        if op == "*": return l * r
        if op == "/": return l / r
        if op == "min": return min(l, r)
        if op == "max": return max(l, r)
        
    if isinstance(expr, Cmp):
        l = eval_expr(expr.left, env)
        r = eval_expr(expr.right, env)
        
        if is_absent(l) or is_absent(r):
            return ABSENT
            
        op = expr.op
        if op == "<": return l < r
        if op == "<=": return l <= r
        if op == "==": return l == r
        if op == ">": return l > r
        if op == ">=": return l >= r
        
    if isinstance(expr, Delay):
        # For instant evaluation (simulation step), Delay acts as a value source.
        # Ideally, the environment should already contain the 'delayed' value for this node?
        # Or we return the default if it's the first step?
        # We don't have state access here easily.
        # Simplest hack: return default. 
        # Real runtime handles Delay by pre-calculating and putting into env.
        return expr.default

    raise ValueError(f"Unknown expression type: {type(expr)}")

def eval_expr_3val(expr: Expr[Any], env: Dict[str, V3[Any]]) -> V3[Any]:
    if isinstance(expr, Const):
        return V3.known(expr.value)
    
    if isinstance(expr, Var):
        return env.get(expr.name, V3.bottom())
    
    if isinstance(expr, If):
        cond = eval_expr_3val(expr.cond, env)
        
        if cond.known:
            if cond.value:
                return eval_expr_3val(expr.then_, env)
            else:
                return eval_expr_3val(expr.else_, env)
        else:
            # Condition unknown -> evaluate both branches
            t = eval_expr_3val(expr.then_, env)
            e = eval_expr_3val(expr.else_, env)
            
            if t.known and e.known and t.value == e.value:
                return t
            return V3.bottom()
            
    if isinstance(expr, BinOp):
        l = eval_expr_3val(expr.left, env)
        r = eval_expr_3val(expr.right, env)
        
        if not l.known or not r.known:
            return V3.bottom()
            
        # Both known, compute standard
        op = expr.op
        lv, rv = l.value, r.value
        # ... copy paste logic from eval_expr or reuse?
        # Reuse logic if possible, but types differ.
        res = None
        if op == "+": res = lv + rv
        elif op == "-": res = lv - rv
        elif op == "*": res = lv * rv
        elif op == "/": res = lv / rv
        elif op == "min": res = min(lv, rv)
        elif op == "max": res = max(lv, rv)
        return V3.known(res)

    if isinstance(expr, Cmp):
        l = eval_expr_3val(expr.left, env)
        r = eval_expr_3val(expr.right, env)
        
        if not l.known or not r.known:
            return V3.bottom()
            
        op = expr.op
        lv, rv = l.value, r.value
        res = None
        if op == "<": res = lv < rv
        elif op == "<=": res = lv <= rv
        elif op == "==": res = lv == rv
        elif op == ">": res = lv > rv
        elif op == ">=": res = lv >= rv
        return V3.known(res)

    if isinstance(expr, Delay):
        # CRITICAL: Delay breaks the instant dependency chain.
        # In 3-valued logic for instant causality, a Delayed value is "Known" (it's the state from previous tick).
        # Even if we don't know the exact value, we know it is PRESENT/DETERMINED for the purpose of the current microstep.
        # So we return Known(default) or Known(Any).
        return V3.known(expr.default)

    return V3.bottom()
