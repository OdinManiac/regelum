import pytest
from rg_compiler.core.dsl import If, Const, Var, BinOp
from rg_compiler.core.ternary import V3
from rg_compiler.core.interpreter import eval_expr_3val

def test_nested_ternary_if():
    inner = If(Var("u"), Const(1), Const(1))
    outer = If(Var("u"), inner, Const(1))
    
    env = {"u": V3.bottom()}
    
    res = eval_expr_3val(outer, env)
    assert res.known
    assert res.value == 1

def test_complex_ternary_resolution():
    from rg_compiler.core.dsl import Cmp
    
    expr = If(
        Cmp(">", Var("x"), Const(0)),
        Const(10),
        Const(10)
    )
    
    env = {"x": V3.bottom()}
    res = eval_expr_3val(expr, env)
    assert res.known
    assert res.value == 10

def test_ternary_arithmetic_propagation():
    # if u then 1 else 2 -> unknown (res1)
    # res1 + 5 -> unknown
    # if u then res1 else res1 -> unknown (because res1 unknown)
    
    # Step 1: inner = If(u, 1, 2) -> Bottom
    inner = If(Var("u"), Const(1), Const(2))
    
    # Step 2: plus = inner + 5 -> Bottom
    plus = BinOp("+", inner, Const(5))
    
    # Step 3: outer = If(u, plus, plus)
    # Since 'plus' is Bottom, both branches are Bottom.
    # Even if branches are structurally identical, their value is unknown.
    # Eval should return Bottom.
    outer = If(Var("u"), plus, plus)
    
    env = {"u": V3.bottom()}
    
    # Verify Step 1
    res1 = eval_expr_3val(inner, env)
    assert not res1.known
    
    # Verify Step 2
    res2 = eval_expr_3val(plus, env)
    assert not res2.known
    
    # Verify Step 3
    res3 = eval_expr_3val(outer, env)
    assert not res3.known
