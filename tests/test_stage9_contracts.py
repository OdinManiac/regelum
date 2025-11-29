import pytest
from regelum.core.ext_node import ExtNode
from regelum.core.contracts import contract, unsafe
from regelum.core.node import Context # Fix import
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig
from regelum.compiler.report import CompilationReport

class ExtNodeWithContract(ExtNode):
    @contract(deterministic=True, monotone=True)
    def step(self, ctx: Context):
        pass

class UnsafeNode(ExtNode):
    @unsafe(reason="I/O")
    def step(self, ctx: Context):
        pass

def test_ext_contract_ir():
    runtime = GraphRuntime()
    node = ExtNodeWithContract("ext1")
    runtime.add_node(node)
    
    compiler = CompilerPipeline(CompilerConfig())
    ir = compiler.build_ir(runtime)
    
    r = ir.nodes["ext1"].reactions[0]
    assert r.contract is not None
    assert r.contract.deterministic
    assert r.contract.monotone
    assert not r.is_unsafe

def test_unsafe_ir():
    runtime = GraphRuntime()
    node = UnsafeNode("unsafe1")
    runtime.add_node(node)
    
    compiler = CompilerPipeline(CompilerConfig())
    ir = compiler.build_ir(runtime)
    
    r = ir.nodes["unsafe1"].reactions[0]
    assert r.is_unsafe
    assert r.unsafe_reason == "I/O"

def test_compilation_report():
    runtime = GraphRuntime()
    node = UnsafeNode("unsafe1")
    runtime.add_node(node)
    
    compiler = CompilerPipeline(CompilerConfig())
    # No passes, so no errors
    ir = compiler.build_ir(runtime)
    result = compiler.run_passes(ir)
    
    report = CompilationReport(ir, result.diagnostics)
    output = str(report)
    assert "Nodes: 1" in output
    assert "Errors: 0" in output
