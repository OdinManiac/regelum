from __future__ import annotations

from typing import Any, Set

from regelum.core.dsl import Expr, Var, If, BinOp, Cmp, Delay


def collect_expr_vars(expr: Expr[Any]) -> Set[str]:
    vars_: Set[str] = set()
    if isinstance(expr, Var):
        vars_.add(expr.name)
    elif isinstance(expr, If):
        vars_.update(collect_expr_vars(expr.cond))
        vars_.update(collect_expr_vars(expr.then_))
        vars_.update(collect_expr_vars(expr.else_))
    elif isinstance(expr, BinOp):
        vars_.update(collect_expr_vars(expr.left))
        vars_.update(collect_expr_vars(expr.right))
    elif isinstance(expr, Cmp):
        vars_.update(collect_expr_vars(expr.left))
        vars_.update(collect_expr_vars(expr.right))
    elif isinstance(expr, Delay):
        # Delay nodes break instant dependency, nothing to add.
        pass
    return vars_

