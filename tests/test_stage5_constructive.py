import pytest
from rg_compiler.core.dsl import Expr, If, Const, Var
from rg_compiler.core.core_node import CoreNode, State, reaction
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import CausalityPass

class CycleNode(CoreNode):
    val = State[int](init=0)
    
    @reaction
    def loop(self, val: Expr[int]) -> Expr[int]:
        # val = val
        # Non-constructive if no init? 
        # State has init=0. So it IS constructive because it starts with 0.
        # Wait, CausalityPass resets SCC vars to Bottom!
        # If logic is `val = val`, and val starts at Bottom, it stays Bottom.
        # So this is non-constructive self-loop!
        # Correct.
        self.val.set(val)
        return val

class ConstructiveNode(CoreNode):
    val = State[int](init=0)
    
    @reaction
    def loop(self, val: Expr[int]) -> Expr[int]:
        # val = 1. Constant.
        # Bottom -> 1.
        # Constructive.
        self.val.set(1)
        return val

def test_non_constructive_cycle():
    runtime = GraphRuntime()
    n = CycleNode("N1")
    runtime.add_node(n)
    
    # Build IR
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(CausalityPass())
    
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    # Should fail with Non-constructive cycle
    # N1.val depends on itself: R reads val -> writes val (via set).
    # AST: set(val). Eval(val) -> Bottom.
    assert not result.success
    d = result.diagnostics[0]
    assert d.code == "CAUS003"

def test_constructive_cycle():
    runtime = GraphRuntime()
    n = ConstructiveNode("N2")
    runtime.add_node(n)
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(CausalityPass())
    
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    # Should pass
    # N2.val depends on itself structurally?
    # Reads val? Yes (arg).
    # Writes val? Yes (set(1)).
    # Cycle in graph? Yes.
    # Check constructive:
    # Iter 1: val=Bottom. Eval(1) -> 1.
    # Update val -> 1.
    # Iter 2: val=1. Eval(1) -> 1. Stable.
    # Result: Known.
    
    assert result.success

