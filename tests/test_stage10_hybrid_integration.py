import os

import pytest

os.environ["RG_DISABLE_FILE_LOGS"] = "1"

from regelum.api import Pipeline
from regelum.core.continuous import ContinuousNode, ContinuousState
from regelum.core.hybrid_adapters import ContinuousWrapper, ZeroOrderHold, Sampler
from regelum.core.node import RawNode, Context


class DrivenIntegrator(ContinuousNode):
    x = ContinuousState(0.0)

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": inputs.get("u", 0.0)}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"]}


class DictSource(RawNode):
    def __init__(self, node_id: str, sequence: list[float]):
        super().__init__(node_id)
        self.out = self.add_output("out")
        self.sequence = sequence
        self.idx = 0

    def step(self, ctx: Context) -> None:
        if self.idx >= len(self.sequence):
            val = self.sequence[-1]
        else:
            val = self.sequence[self.idx]
        self.idx += 1
        ctx.write(self.out, {"u": val})


class DictSink(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("inp")
        self.last = None

    def step(self, ctx: Context) -> None:
        self.last = ctx.read(self.inp)


def test_hybrid_pipeline_with_zoh_and_sampler():
    # Sequence: first 5 ticks u=2.0, then u=0.0; dt=0.05
    driver = DictSource("driver", [2.0] * 5 + [0.0])
    zoh = ZeroOrderHold("zoh", init=0.0)
    inner = DrivenIntegrator("ct")
    inner.max_step = 0.1
    wrapper = ContinuousWrapper("wrap", inner, default_dt=0.05)
    sampler = Sampler("sampler")
    sink = DictSink("sink")

    pipe = Pipeline(mode="strict")
    pipe.add(driver, zoh, wrapper, sampler, sink)

    driver.o.out >> zoh.i.inp
    zoh.o.out >> wrapper.i.u
    wrapper.o.y >> sampler.i.inp
    sampler.o.out >> sink.i.inp

    assert pipe.compile()

    # Run 10 ticks: first 5 with u=2.0 (integral adds 2*0.05 each tick),
    # next 5 with u=0.0 (state holds).
    pipe.run(ticks=10)

    assert sink.last is not None
    final_x = sink.last.get("x")
    expected = 5 * 2.0 * 0.05
    assert final_x == pytest.approx(expected, rel=1e-3, abs=1e-4)


def test_hybrid_pipeline_injects_global_dt():
    driver = DictSource("driver_dt", [1.0])
    zoh = ZeroOrderHold("zoh_dt", init=1.0)
    inner = DrivenIntegrator("ct_dt")
    inner.max_step = 0.2
    wrapper = ContinuousWrapper("wrap_dt", inner, default_dt=0.01)  # will be overridden by global dt
    sampler = Sampler("sampler_dt")
    sink = DictSink("sink_dt")

    pipe = Pipeline(mode="strict")
    pipe.add(driver, zoh, wrapper, sampler, sink)

    driver.o.out >> zoh.i.inp
    zoh.o.out >> wrapper.i.u
    wrapper.o.y >> sampler.i.inp
    sampler.o.out >> sink.i.inp

    assert pipe.compile()

    pipe.run(ticks=10, dt=0.1)
    assert sink.last is not None
    final_x = sink.last.get("x")
    assert final_x == pytest.approx(1.0, rel=1e-3, abs=1e-4)
    assert pipe.runtime.current_time == pytest.approx(1.0, rel=1e-9)
