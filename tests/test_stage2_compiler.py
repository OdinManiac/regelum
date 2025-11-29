import pytest
from regelum.core.node import RawNode, Context
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig, DiagnosticSeverity
from regelum.compiler.passes import StructuralPass

class SimpleNode(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.input = self.add_input("in")
        self.output = self.add_output("out")

    def step(self, ctx: Context) -> None:
        pass

def test_build_ir():
    runtime = GraphRuntime()
    n1 = SimpleNode("N1")
    n2 = SimpleNode("N2")
    runtime.add_node(n1)
    runtime.add_node(n2)
    runtime.connect(n1.output, n2.input)
    
    compiler = CompilerPipeline(CompilerConfig())
    ir = compiler.build_ir(runtime)
    
    assert "N1" in ir.nodes
    assert "N2" in ir.nodes
    assert len(ir.edges) == 1
    edge = ir.edges[0]
    assert edge.src_node == "N1" and edge.src_port == "out"
    assert edge.dst_node == "N2" and edge.dst_port == "in"
    
    assert "in" in ir.nodes["N1"].inputs
    assert "out" in ir.nodes["N1"].outputs

def test_structural_pass_unconnected():
    runtime = GraphRuntime()
    n1 = SimpleNode("N1") # Has 'in' unconnected
    runtime.add_node(n1)
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(StructuralPass())
    
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    assert not result.success
    assert len(result.diagnostics) == 1
    d = result.diagnostics[0]
    assert d.severity == DiagnosticSeverity.ERROR
    assert d.code == "STRUCT001"
    assert "unconnected" in d.message

def test_structural_pass_connected():
    runtime = GraphRuntime()
    n1 = SimpleNode("N1")
    n2 = SimpleNode("N2")
    # N1.in unconnected -> Error
    # N1.out -> N2.in 
    # N2.out unconnected -> OK (outputs can be dangling)
    
    runtime.add_node(n1)
    runtime.add_node(n2)
    runtime.connect(n1.output, n2.input)
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(StructuralPass())
    
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    # Should fail because N1.in is unconnected
    assert not result.success
    
    # Connect something to N1.in to fix
    # For test, let's assume N1 is Source (no input)
    class Source(RawNode):
        def __init__(self, nid):
            super().__init__(nid)
            self.out = self.add_output("out")
        def step(self, ctx): pass
        
    runtime2 = GraphRuntime()
    s = Source("S")
    n = SimpleNode("N")
    runtime2.add_node(s)
    runtime2.add_node(n)
    runtime2.connect(s.out, n.input)
    
    ir2 = compiler.build_ir(runtime2)
    result2 = compiler.run_passes(ir2)
    
    assert result2.success
    assert len(result2.diagnostics) == 0

