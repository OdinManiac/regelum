import pytest
from rg_compiler.core.core_node import CoreNode, Input, Output
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig, DiagnosticSeverity
from rg_compiler.compiler.passes import TypeCheckPass, WriteConflictPass
from rg_compiler.ir.graph import IRReaction, IRVariable

class IntNode(CoreNode):
    x: Input[int] = Input[int]()
    y: Output[int] = Output[int]()

class FloatNode(CoreNode):
    x: Input[float] = Input[float]()
    y: Output[float] = Output[float]()

def test_type_check_pass():
    runtime = GraphRuntime()
    n1 = IntNode("N1")
    n2 = FloatNode("N2")
    runtime.add_node(n1)
    runtime.add_node(n2)
    
    # Connect N1.y (int) -> N2.x (float)
    runtime.connect(n1.outputs["y"], n2.inputs["x"])
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(TypeCheckPass())
    
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    # Expect warning/error depending on implementation
    # My implementation does str(hint).
    # Input[int] str might be "rg_compiler...Input[int]"
    # Let's check what diagnostics we get.
    
    assert len(result.diagnostics) > 0
    d = result.diagnostics[0]
    assert d.code == "TYPE001"
    assert "Type mismatch" in d.message

def test_write_conflict_pass_manual():
    # Manually build IR with conflict
    from rg_compiler.ir.graph import IRGraph, IRNode
    
    ir = IRGraph()
    ir.variables["v1"] = IRVariable(name="v1", policy="ErrorPolicy")
    
    # Node 1 writes v1
    r1 = IRReaction(id="r1", writes_vars={"v1"})
    n1 = IRNode(id="N1", kind="Core", reactions=[r1])
    ir.nodes["N1"] = n1
    
    # Node 2 writes v1
    r2 = IRReaction(id="r2", writes_vars={"v1"})
    n2 = IRNode(id="N2", kind="Core", reactions=[r2])
    ir.nodes["N2"] = n2
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(WriteConflictPass())
    
    result = compiler.run_passes(ir)
    
    assert not result.success
    d = result.diagnostics[0]
    assert d.code == "WRITE001"
    assert "Multiple writers" in d.message

