import pytest
from rg_compiler.core.runtime import GraphRuntime, ZenoRuntimeError
from rg_compiler.core.node import RawNode, Context
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.ir.graph import IRGraph, IRNode, IRReaction, IRVariable
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import NonZenoPass


def test_non_zeno_pass_requires_rank():
    ir = IRGraph()
    ir.variables["v1"] = IRVariable(
        name="v1",
        policy="ErrorPolicy",
        has_init=True,
        allows_multiwriter=False,
        is_monotone=True,
        height_bound=1,
    )
    r = IRReaction(id="step", reads_vars={"v1"}, writes_vars={"v1"})
    node = IRNode(id="N", kind="Core", reactions=[r])
    ir.nodes["N"] = node
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(NonZenoPass())
    res = compiler.run_passes(ir)
    assert not res.success
    errors = [d.code for d in res.diagnostics if d.severity.name == "ERROR"]
    assert "ZEN001" in errors


def test_non_zeno_pass_accepts_rank():
    ir = IRGraph()
    ir.variables["v1"] = IRVariable(
        name="v1",
        policy="ErrorPolicy",
        has_init=True,
        allows_multiwriter=False,
        is_monotone=True,
        height_bound=1,
    )
    r = IRReaction(
        id="step",
        reads_vars={"v1"},
        writes_vars={"v1"},
        nonzeno_rank="v1",
        nonzeno_limit=5,
    )
    node = IRNode(id="N", kind="Core", reactions=[r])
    ir.nodes["N"] = node
    
    compiler = CompilerPipeline(CompilerConfig())
    compiler.add_pass(NonZenoPass())
    res = compiler.run_passes(ir)
    assert res.success


class Oscillator(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("loop", default=0)
        self.out = self.add_output("loop")
        self._toggle = 0
    
    def step(self, ctx: Context):
        self._toggle = 1 - self._toggle
        ctx.write(self.out, self._toggle)
        ctx.read(self.inp)


def test_runtime_zeno_guard_raises():
    runtime = GraphRuntime()
    osc = Oscillator("osc")
    runtime.add_node(osc)
    runtime.connect(osc.o.loop, osc.i.loop)
    runtime.build_schedule()
    
    with pytest.raises(ZenoRuntimeError):
        runtime.run_tick()
