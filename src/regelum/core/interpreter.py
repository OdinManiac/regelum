from typing import Any, Dict
from .dsl import Expr, Const, Var, If, BinOp, Cmp, Delay
from .ternary import V3, Presence
from .values import ABSENT, is_absent

def eval_expr(expr: Expr[Any], env: Dict[str, Any]) -> Any:
    if isinstance(expr, Const):
        return expr.value
    
    if isinstance(expr, Var):
        return env.get(expr.name, ABSENT)
        
    if isinstance(expr, If):
        cond = eval_expr(expr.cond, env)
        if is_absent(cond):
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
        raise RuntimeError("Delay expressions must be lowered before interpretation.")

    raise ValueError(f"Unknown expression type: {type(expr)}")

def eval_expr_3val(expr: Expr[Any], env: Dict[str, V3[Any]]) -> V3[Any]:
    if isinstance(expr, Const):
        return V3.present(expr.value)
    
    if isinstance(expr, Var):
        return env.get(expr.name, V3.bottom())
    
    if isinstance(expr, If):
        cond = eval_expr_3val(expr.cond, env)
        
        if cond.presence == Presence.BOTTOM:
            t = eval_expr_3val(expr.then_, env)
            e = eval_expr_3val(expr.else_, env)
            if t.presence == Presence.PRESENT and e.presence == Presence.PRESENT and t.value == e.value:
                return t
            if t.presence == Presence.ABSENT and e.presence == Presence.ABSENT:
                return V3.absent()
            return V3.bottom()
        
        if cond.presence == Presence.ABSENT:
            return V3.absent()
        
        if cond.value:
            return eval_expr_3val(expr.then_, env)
        return eval_expr_3val(expr.else_, env)
            
    if isinstance(expr, BinOp):
        l = eval_expr_3val(expr.left, env)
        r = eval_expr_3val(expr.right, env)
        
        if l.presence == Presence.BOTTOM or r.presence == Presence.BOTTOM:
            return V3.bottom()
        if l.presence == Presence.ABSENT or r.presence == Presence.ABSENT:
            return V3.absent()
            
        # Both known, compute standard
        op = expr.op
        lv, rv = l.value, r.value
        res = None
        if op == "+": res = lv + rv
        elif op == "-": res = lv - rv
        elif op == "*": res = lv * rv
        elif op == "/": res = lv / rv
        elif op == "min": res = min(lv, rv)
        elif op == "max": res = max(lv, rv)
        return V3.present(res)

    if isinstance(expr, Cmp):
        l = eval_expr_3val(expr.left, env)
        r = eval_expr_3val(expr.right, env)
        
        if l.presence == Presence.BOTTOM or r.presence == Presence.BOTTOM:
            return V3.bottom()
        if l.presence == Presence.ABSENT or r.presence == Presence.ABSENT:
            return V3.absent()
            
        op = expr.op
        lv, rv = l.value, r.value
        res = None
        if op == "<": res = lv < rv
        elif op == "<=": res = lv <= rv
        elif op == "==": res = lv == rv
        elif op == ">": res = lv > rv
        elif op == ">=": res = lv >= rv
        return V3.present(res)

    if isinstance(expr, Delay):
        raise RuntimeError("Delay expressions must be lowered before ternary evaluation.")

    return V3.bottom()
