import pytest
from regelum.core.dsl import If, Const, Var, BinOp
from regelum.core.ternary import V3
from regelum.core.interpreter import eval_expr_3val
from regelum.ir.graph import IRGraph, IRNode, IRReaction, IREdge
from regelum.compiler.pipeline import DiagnosticSink
from regelum.compiler.passes import CausalityPass

def test_eval_3val_if_merge():
    # if (unknown) then 1 else 1 -> 1
    # if (unknown) then 1 else 2 -> unknown
    
    expr1 = If(Var("c"), Const(1), Const(1))
    env = {"c": V3.bottom()}
    res1 = eval_expr_3val(expr1, env)
    assert res1.known
    assert res1.value == 1
    
    expr2 = If(Var("c"), Const(1), Const(2))
    res2 = eval_expr_3val(expr2, env)
    assert not res2.known

def test_causality_pass_cycle():
    # Cycle: N1 -> N2 -> N1
    ir = IRGraph()
    
    r1 = IRReaction(id="r1")
    n1 = IRNode(id="N1", kind="Raw", reactions=[r1])
    ir.nodes["N1"] = n1
    
    r2 = IRReaction(id="r2")
    n2 = IRNode(id="N2", kind="Raw", reactions=[r2])
    ir.nodes["N2"] = n2
    
    # Edges create cycle
    ir.edges.append(IREdge("N1", "out", "N2", "in"))
    ir.edges.append(IREdge("N2", "out", "N1", "in"))
    
    diag = DiagnosticSink()
    pass_ = CausalityPass()
    pass_.run(ir, diag)
    
    assert len(diag.diagnostics) > 0
    d = diag.diagnostics[0]
    assert d.code == "CAUS001"
    assert "Algebraic cycle" in d.message

def test_causality_pass_no_cycle():
    # N1 -> N2
    ir = IRGraph()
    
    r1 = IRReaction(id="r1")
    n1 = IRNode(id="N1", kind="Raw", reactions=[r1])
    ir.nodes["N1"] = n1
    
    r2 = IRReaction(id="r2")
    n2 = IRNode(id="N2", kind="Raw", reactions=[r2])
    ir.nodes["N2"] = n2
    
    ir.edges.append(IREdge("N1", "out", "N2", "in"))
    
    diag = DiagnosticSink()
    pass_ = CausalityPass()
    pass_.run(ir, diag)
    
    assert len(diag.diagnostics) == 0

