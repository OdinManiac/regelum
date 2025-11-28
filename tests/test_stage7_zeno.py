import pytest
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import NonZenoPass

def test_non_zeno_pass():
    # Just verify it runs for now
    runtime = GraphRuntime()
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(NonZenoPass())
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    assert res.success

