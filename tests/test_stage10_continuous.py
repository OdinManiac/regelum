import math
import os

import pytest

os.environ["RG_DISABLE_FILE_LOGS"] = "1"

from rg_compiler.core.continuous import (
    ContinuousNode,
    ContinuousRuntime,
    ContinuousState,
    INTEGRATOR_EULER,
    INTEGRATOR_RK4,
)
from rg_compiler.core.hybrid_adapters import ContinuousWrapper
from rg_compiler.api import Pipeline
from rg_compiler.core.node import RawNode, Context


class DecayNode(ContinuousNode):
    x = ContinuousState(1.0)
    k: float = 1.5

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": -self.k * state["x"]}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"]}


class DrivenNode(ContinuousNode):
    x = ContinuousState(0.0)

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        u = inputs.get("u", 0.0)
        return {"x": u}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"]}


def test_rk4_decay_matches_exponential():
    node = DecayNode("decay")
    runtime = ContinuousRuntime()
    runtime.add_node(node)

    runtime.run(total_time=1.0, dt=0.001)

    expected = math.exp(-node.k * 1.0)
    state = runtime.get_state(node.id)
    assert abs(state["x"] - expected) < 1e-3
    trace = runtime.traces[node.id]
    assert trace[0][0] == 0.0
    assert trace[-1][0] == pytest.approx(1.0, rel=1e-6)


def test_rk4_more_accurate_than_euler():
    rk4_node = DecayNode("rk4")
    rk4_node.integrator = INTEGRATOR_RK4
    rk4_node.max_step = 0.1

    euler_node = DecayNode("euler")
    euler_node.integrator = INTEGRATOR_EULER
    euler_node.max_step = 0.1

    rk4_rt = ContinuousRuntime()
    euler_rt = ContinuousRuntime()
    rk4_rt.add_node(rk4_node)
    euler_rt.add_node(euler_node)

    rk4_rt.run(total_time=1.0, dt=0.05)
    euler_rt.run(total_time=1.0, dt=0.05)

    expected = math.exp(-rk4_node.k * 1.0)
    rk4_err = abs(rk4_rt.get_state(rk4_node.id)["x"] - expected)
    euler_err = abs(euler_rt.get_state(euler_node.id)["x"] - expected)
    assert rk4_err < euler_err


def test_step_respects_max_step():
    node = DecayNode("limited")
    node.max_step = 0.005
    runtime = ContinuousRuntime()
    runtime.add_node(node)

    with pytest.raises(ValueError):
        runtime.step(0.01)


def test_continuous_pass_accepts_wrapper():
    wrapper = ContinuousWrapper("cw", DecayNode("inner"), default_dt=0.01)
    pipe = Pipeline(mode="strict")
    pipe.add(wrapper)
    assert pipe.compile()


def test_continuous_pass_rejects_zero_dt():
    wrapper = ContinuousWrapper("cw_bad", DecayNode("inner_bad"), default_dt=0.0)
    pipe = Pipeline(mode="strict")
    pipe.add(wrapper)
    ok = pipe.compile()
    assert not ok
    errors = [d.code for d in pipe.report.diagnostics if d.severity.name == "ERROR"]
    assert "CT002" in errors or "CT001" in errors


class StateSink(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("inp")
        self.last = None

    def step(self, ctx: Context) -> None:
        self.last = ctx.read(self.inp)


def test_wrapper_applies_control_input():
    inner = DrivenNode("ctrl_inner")
    inner.max_step = 0.2
    wrapper = ContinuousWrapper("cw_ctrl", inner, default_dt=0.1)
    sink = StateSink("sink")
    pipe = Pipeline(mode="strict")
    pipe.add(wrapper, sink)
    wrapper.o.state >> sink.i.inp

    compiled = pipe.compile()
    assert compiled

    pipe.run(ticks=10, inputs={wrapper.i.u: {"u": 2.0}})
    assert sink.last is not None
    assert sink.last.get("x") == pytest.approx(2.0, rel=1e-2)
