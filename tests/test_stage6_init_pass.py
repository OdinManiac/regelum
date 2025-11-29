import pytest
from regelum.core.core_node import CoreNode, State, reaction
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig
from regelum.compiler.passes import InitPass

def test_init_pass_runs():
    runtime = GraphRuntime()
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(InitPass())
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    assert res.success

def test_init_pass_strict_missing_init():
    class BadInitNode(CoreNode):
        # Pass init=None to simulate missing init
        val = State(init=None) 
        
        @reaction
        def step(self):
            pass
            
    runtime = GraphRuntime()
    node = BadInitNode("bad")
    runtime.add_node(node)
    
    # Strict mode -> Error
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(InitPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    assert not res.success
    assert any(d.code == "INIT001" for d in res.diagnostics)

def test_init_pass_lenient_missing_init():
    class BadInitNode(CoreNode):
        val = State(init=None)
        @reaction
        def step(self): pass
            
    runtime = GraphRuntime()
    node = BadInitNode("bad_lenient")
    runtime.add_node(node)
    
    # Default/Best Effort -> Ignored
    compiler = CompilerPipeline(CompilerConfig(mode="best_effort"))
    compiler.add_pass(InitPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    assert res.success
