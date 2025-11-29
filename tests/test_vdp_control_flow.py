import os
from typing import Tuple

import pytest

os.environ["RG_DISABLE_FILE_LOGS"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regelum.api import Pipeline
from regelum.core.node import RawNode, Context

from pipes.van_der_pol_hybrid import (
    ControlDelay,
    PDController,
    StateDelay,
    Splitter,
    VanDerPolContinuous,
    DT,
)


class ValueSink(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("inp")
        self.last = None

    def step(self, ctx: Context) -> None:
        self.last = ctx.read(self.inp)


def build_headless_pipe(u_limit: float) -> Tuple[Pipeline, ValueSink]:
    plant = VanDerPolContinuous("Plant").as_hybrid(
        dt=DT,
        hold_init=0.0,
        u_name="u",
        y_name="plant_state",
        state_name="continuous_state",
    )
    delay = StateDelay("Delay")
    split = Splitter("Split")
    ctrl = PDController("Controller", target_x=0.0, kp=5.5, kd=0.8, u_limit=u_limit)
    u_delay = ControlDelay("ControlDelay")
    sink = ValueSink("Sink")

    pipe = Pipeline(mode="strict")
    pipe.add(plant, delay, split, ctrl, u_delay, sink)

    # Sink is not auto-wired; rest is matched by names.
    u_delay.outputs["u"] >> sink.inputs["inp"]
    pipe.auto_wire(strict=True)

    assert pipe.compile()
    return pipe, sink


def test_control_signal_respects_limit():
    pipe_zero, sink_zero = build_headless_pipe(u_limit=0.0)
    pipe_limited, sink_limited = build_headless_pipe(u_limit=15.0)

    pipe_zero.run(ticks=2, dt=DT)
    pipe_limited.run(ticks=2, dt=DT)

    assert sink_zero.last is not None
    assert sink_zero.last == pytest.approx(0.0, abs=1e-9)

    assert sink_limited.last is not None
    assert sink_limited.last == pytest.approx(-5.5, rel=0.0, abs=1e-6)
