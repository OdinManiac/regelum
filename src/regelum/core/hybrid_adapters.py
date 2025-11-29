from __future__ import annotations

from typing import Dict, Optional

from .continuous import ContinuousNode, ContinuousRuntime
from .core_node import Input, Output
from .node import Context, RawNode
from .values import ABSENT, is_absent


class ZeroOrderHold(RawNode):
    """
    Holds the last discrete value as a constant for continuous time.
    Produces the held value on its output every tick.
    """

    def __init__(self, node_id: str, *, init: float = 0.0):
        super().__init__(node_id)
        self.inp = self.add_input("inp", default=init)
        self.out = self.add_output("out")
        self._held = init

    def step(self, ctx: Context) -> None:
        val = ctx.read(self.inp)
        if not is_absent(val):
            self._held = val
        ctx.write(self.out, self._held)


class Sampler(RawNode):
    """
    Samples a continuous output each tick.
    Expects to be driven by a ContinuousWrapper via a dedicated input.
    """

    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("inp")
        self.out = self.add_output("out")

    def step(self, ctx: Context) -> None:
        ctx.write(self.out, ctx.read(self.inp))


class ContinuousWrapper(RawNode):
    """
    Embeds a single ContinuousNode into the discrete runtime.
    - Input 'dt' (default=0.01) controls integration step for this tick.
    - Input 'u' is passed as a zero-order-held parameter map (dict[str, float]).
    - Outputs:
        * 'state': dict of continuous states after integration.
        * 'y': dict of outputs computed at the end of the step.
    """

    def __init__(
        self,
        node_id: str,
        inner: ContinuousNode,
        *,
        default_dt: float = 0.01,
        u_name: str = "u",
        state_name: str = "state",
        y_name: str = "y",
    ):
        super().__init__(node_id)
        self.inner = inner
        self.dt = self.add_input("dt", default=default_dt)
        self.u = self.add_input(u_name, default={})
        self.state_out = self.add_output(state_name)
        self.y_out = self.add_output(y_name)
        self._rt = ContinuousRuntime()
        self._rt.add_node(inner)

    def step(self, ctx: Context) -> None:
        dt = ctx.read(self.dt)
        if dt is ABSENT:
            dt = 0.0
        u_val = ctx.read(self.u)
        if u_val is ABSENT:
            u_val = {}
        if not isinstance(u_val, dict):
            u_val = {"u": u_val}
        if dt > 0.0:
            self._rt.step(dt, {self.inner.id: u_val})
        else:
            self._rt.last_inputs[self.inner.id] = u_val
        state_snapshot = self._rt.get_state(self.inner.id)
        outputs = self._rt.outputs(self.inner.id)
        ctx.write(self.state_out, state_snapshot)
        ctx.write(self.y_out, outputs)


class HybridContinuousWrapper(ContinuousWrapper):
    """
    Single-node hybrid adapter: embeds ContinuousWrapper and ZOH semantics on the 'u' input.
    - Input 'u' is held across ticks (last non-ABSENT value), init via hold_init.
    - Input 'dt' controls integration step (default from constructor).
    - Outputs: 'state' (dict of continuous states), 'y' (dict of outputs).
    """

    def __init__(
        self,
        node_id: str,
        inner: ContinuousNode,
        *,
        default_dt: float = 0.01,
        hold_init: float | dict = 0.0,
        u_name: str = "u",
        state_name: str = "state",
        y_name: str = "y",
    ):
        super().__init__(node_id, inner, default_dt=default_dt, u_name=u_name, state_name=state_name, y_name=y_name)
        if isinstance(hold_init, dict):
            self._held_u: dict = hold_init
        else:
            self._held_u = {"u": hold_init}

    def step(self, ctx: Context) -> None:
        dt = ctx.read(self.dt)
        if dt is ABSENT:
            dt = 0.0

        u_val = ctx.read(self.u)
        if u_val is ABSENT:
            u_norm = self._held_u
        else:
            if not isinstance(u_val, dict):
                u_norm = {"u": u_val}
            else:
                u_norm = u_val
            self._held_u = u_norm

        if dt > 0.0:
            self._rt.step(dt, {self.inner.id: u_norm})
        else:
            self._rt.last_inputs[self.inner.id] = u_norm
        state_snapshot = self._rt.get_state(self.inner.id)
        outputs = self._rt.outputs(self.inner.id)
        ctx.write(self.state_out, state_snapshot)
        ctx.write(self.y_out, outputs)
