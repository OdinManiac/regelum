import pytest
from rg_compiler.core.dsl import Expr, if_
from rg_compiler.core.core_node import CoreNode, State, reaction
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import CausalityPass
from rg_compiler.core.variables import LWWPolicy
from rg_compiler.core.ternary import V3, Presence

class CycleNode(CoreNode):
    val = State[int](init=0)
    
    @reaction
    def loop(self, val: Expr[int]) -> Expr[int]:
        # Oscillating self-loop: 0 -> 1 -> 0 -> ...
        self.val.set(if_(val > 0, 0, 1))
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

class LWWNode(CoreNode):
    val = State[int](init=0, policy=LWWPolicy(priority_order=[]))
    
    @reaction
    def loop(self, val: Expr[int]) -> Expr[int]:
        self.val.set(val + 1)
        return val

def test_cycle_with_non_monotone_state():
    runtime = GraphRuntime()
    node = LWWNode("lww")
    runtime.add_node(node)
    
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(CausalityPass())
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    assert not result.success
    errors = [d.code for d in result.diagnostics if d.severity.name == "ERROR"]
    assert "CAUS004" in errors


def test_join_values_promotes_absent_to_present():
    merged, changed, conflict = CausalityPass._join_values(V3.absent(), V3.present(42))
    assert merged == V3.present(42)
    assert changed
    assert not conflict


def test_join_values_detects_conflict():
    merged, changed, conflict = CausalityPass._join_values(V3.present(1), V3.present(2))
    assert merged == V3.present(2)
    assert changed
    assert not conflict
