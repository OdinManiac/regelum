import pytest
from regelum.core.ext_node import ExtNode
from regelum.core.contracts import contract
from regelum.core.node import Context
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig
from regelum.compiler.passes import CausalityPass

class LoopBreaker(ExtNode):
    def __init__(self, id):
        super().__init__(id)
        self.add_input("in")
        self.add_output("out")
        
    @contract(no_instant_loop=True)
    def step(self, ctx: Context):
        val = ctx.read(self.inputs["in"])
        ctx.write(self.outputs["out"], val) # Logic doesn't matter for static analysis

class SimpleNode(ExtNode):
    def __init__(self, id):
        super().__init__(id)
        self.add_input("in")
        self.add_output("out")
        
    @contract(no_instant_loop=False) # Default
    def step(self, ctx: Context):
        pass

def test_contract_breaks_cycle():
    # A -> B -> A.
    # A is simple. B is LoopBreaker.
    # Structural cycle exists.
    # CausalityPass should see B breaks the loop.
    
    runtime = GraphRuntime()
    a = SimpleNode("A")
    b = LoopBreaker("B")
    runtime.add_node(a)
    runtime.add_node(b)
    
    runtime.connect(a.outputs["out"], b.inputs["in"])
    runtime.connect(b.outputs["out"], a.inputs["in"])
    
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(CausalityPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    assert res.success
    assert len(res.diagnostics) == 0

def test_contract_respects_cycle():
    # A -> B -> A. Both simple.
    # Should fail with CAUS001.
    
    runtime = GraphRuntime()
    a = SimpleNode("A")
    b = SimpleNode("B")
    runtime.add_node(a)
    runtime.add_node(b)
    
    runtime.connect(a.outputs["out"], b.inputs["in"])
    runtime.connect(b.outputs["out"], a.inputs["in"])
    
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(CausalityPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    assert not res.success
    assert res.diagnostics[0].code == "CAUS001"

