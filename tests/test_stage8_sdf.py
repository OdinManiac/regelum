import pytest
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes_sdf import SDFPass

class Producer(CoreNode):
    out = Output[int](rate=2)
    @reaction
    def produce(self) -> Expr[int]: return 1

class Consumer(CoreNode):
    inp = Input[int](rate=3)
    @reaction
    def consume(self, inp: Expr[int]): pass

class Middle(CoreNode):
    inp = Input[int](rate=1)
    out = Output[int](rate=1)
    @reaction
    def relay(self, inp: Expr[int]) -> Expr[int]: return inp

def test_sdf_consistent():
    # P (rate 2) -> C (rate 3)
    # Solution: q_p * 2 = q_c * 3 => q_p=3, q_c=2. Consistent.
    runtime = GraphRuntime()
    p = Producer("P")
    c = Consumer("C")
    runtime.add_node(p)
    runtime.add_node(c)
    runtime.connect(p.outputs["out"], c.inputs["inp"])
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(SDFPass())
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    assert res.success

def test_sdf_inconsistent():
    # P1 (rate 2) -> C (rate 3) <- P2 (rate 2)
    # q_p1 * 2 = q_c * 3
    # q_p2 * 2 = q_c * 3
    # This is consistent if q_p1 = q_p2.
    
    # Let's make loop inconsistency.
    # A (out:2) -> B (in:1, out:1) -> A (in:1)
    # q_a * 2 = q_b * 1 => q_b = 2 * q_a
    # q_b * 1 = q_a * 1 => q_b = q_a
    # 2 * q_a = q_a => q_a = 0 (trivial only).
    # Need q > 0. So inconsistent.
    
    class NodeA(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=2)
        @reaction
        def step(self, inp: Expr[int]) -> Expr[int]: return inp
        
    class NodeB(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=1)
        @reaction
        def step(self, inp: Expr[int]) -> Expr[int]: return inp
        
    runtime = GraphRuntime()
    a = NodeA("A")
    b = NodeB("B")
    runtime.add_node(a)
    runtime.add_node(b)
    runtime.connect(a.outputs["out"], b.inputs["inp"])
    runtime.connect(b.outputs["out"], a.inputs["inp"])
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(SDFPass())
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    assert not res.success
    assert res.diagnostics[0].code == "SDF001"

