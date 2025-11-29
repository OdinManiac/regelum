from __future__ import annotations

import math
from typing import Sequence

import pygame
import numpy as np

from rg_compiler.api import Pipeline
from rg_compiler.core.continuous import ContinuousNode, ContinuousState
from rg_compiler.core.contracts import contract
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.dsl import Expr, Delay, If
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.node import Context
from rg_compiler.vis import DashboardPlotter, DashboardSignal, build_dashboard

DT = 0.01
GRAVITY = 9.81
LENGTH = 1.0
TARGET_ANGLE = math.pi
MAX_FORCE = 500.0
MPC_HORIZON = 30
MPC_W_WEIGHT = 0.1
MPC_U_WEIGHT = 1e-4
MPC_CANDIDATES: tuple[float, ...] = np.linspace(-20.0, 20.0, 20)
OMEGA_LIMIT = 30.0


def draw_pendulum(surface: pygame.Surface, rect: pygame.Rect, values: dict[str, float], t: float) -> None:
    theta = values.get("theta", 0.0)
    pivot_x = rect.left + rect.width // 2
    pivot_y = rect.top + rect.height // 2
    arm_len = int(min(rect.height, rect.width) * 0.35)
    bob_radius = max(6, arm_len // 14)
    bob_x = pivot_x + int(arm_len * math.sin(theta))
    bob_y = pivot_y + int(arm_len * math.cos(theta))

    pygame.draw.rect(surface, (14, 14, 18), rect)
    pygame.draw.line(surface, (120, 120, 130), (pivot_x, pivot_y), (bob_x, bob_y), 3)
    pygame.draw.circle(surface, (230, 230, 240), (pivot_x, pivot_y), 6)
    pygame.draw.circle(surface, (255, 99, 71), (bob_x, bob_y), bob_radius)


class PendulumContinuous(ContinuousNode):
    theta = ContinuousState(0.1)
    omega = ContinuousState(0.0)

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        theta_val = state["theta"]
        omega_val = state["omega"]
        force = inputs.get("force", inputs.get("u", 0.0))
        dtheta = omega_val
        domega = -(GRAVITY / LENGTH) * math.sin(theta_val) + force
        return {"theta": dtheta, "omega": domega}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"theta": state["theta"], "omega": state["omega"]}


class Splitter(ExtNode):
    plant_state_delayed = Input[dict](default={"theta": 0.0, "omega": 0.0})
    theta = Output[float]()
    omega = Output[float]()

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        s = ctx.read(self.inputs["plant_state_delayed"])
        theta_val = s.get("theta", 0.0)
        omega_val = s.get("omega", 0.0)
        ctx.write(self.outputs["theta"], theta_val)
        ctx.write(self.outputs["omega"], omega_val)


class StateDelay(CoreNode):
    plant_state = Input[dict](default={"theta": 0.1, "omega": 0.0})
    plant_state_delayed = Output[dict]()

    @reaction
    def feed(self, plant_state: Expr[dict]) -> Expr[dict]:
        return Delay(plant_state, default={"theta": 0.1, "omega": 0.0})


class MPCController(ExtNode):
    theta = Input[float]()
    omega = Input[float]()
    force = Output[float]()

    def __init__(
        self,
        node_id: str,
        *,
        horizon: int = MPC_HORIZON,
        dt: float = DT,
        candidate_forces: Sequence[float] | None = None,
        u_weight: float = MPC_U_WEIGHT,
        w_weight: float = MPC_W_WEIGHT,
        omega_limit: float = OMEGA_LIMIT,
    ):
        super().__init__(node_id)
        self._horizon = horizon
        self._dt = dt
        self._u_weight = u_weight
        self._w_weight = w_weight
        self._omega_limit = omega_limit
        if candidate_forces is None:
            self._candidates = MPC_CANDIDATES
        else:
            self._candidates = tuple(candidate_forces)

    def _wrap(self, angle: float) -> float:
        return ((angle + math.pi) % (2.0 * math.pi)) - math.pi

    def _simulate(self, theta: float, omega: float, u: float) -> tuple[float, float]:
        dtheta = omega
        domega = -(GRAVITY / LENGTH) * math.sin(theta) + u
        next_theta = theta + dtheta * self._dt
        next_omega = omega + domega * self._dt
        if next_omega > self._omega_limit:
            bounded_omega = self._omega_limit
        elif next_omega < -self._omega_limit:
            bounded_omega = -self._omega_limit
        else:
            bounded_omega = next_omega
        return self._wrap(next_theta), bounded_omega

    def _rollout_cost(self, theta: float, omega: float, u0: float) -> float:
        cost = 0.0
        t = theta
        w = omega
        u = u0
        for _ in range(self._horizon):
            t, w = self._simulate(t, w, u)
            err = self._wrap(t - TARGET_ANGLE)
            stage = err * err + self._w_weight * w * w + self._u_weight * u * u
            cost += stage
        return cost

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        theta_val = ctx.read(self.inputs["theta"])
        omega_val = ctx.read(self.inputs["omega"])

        best_u = 0.0
        best_cost = math.inf
        for cand in self._candidates:
            c = self._rollout_cost(theta_val, omega_val, cand)
            if c < best_cost:
                best_cost = c
                best_u = cand

        ctx.write(self.outputs["force"], best_u)


def build_pipeline(dt: float = DT) -> Pipeline:
    plant = PendulumContinuous("Plant").as_hybrid(
        dt=dt,
        hold_init=0.0,
        u_name="force",
        y_name="plant_state",
        state_name="continuous_state",
    )
    ctrl = MPCController("MPC", dt=dt, horizon=MPC_HORIZON)
    split = Splitter("Split")
    delay = StateDelay("Delay")

    dash = build_dashboard(
        "Dashboard",
        [
            DashboardSignal("theta", "theta (rad)", (255, 99, 71)),
            DashboardSignal("omega", "omega (rad/s)", (54, 162, 235)),
            DashboardSignal("force", "force", (75, 192, 192)),
        ],
        time_window=None,
        custom_anim=draw_pendulum,
    )

    pipe = Pipeline(mode="strict")
    pipe.add(plant, ctrl, split, delay, dash)
    pipe.auto_wire(strict=True)
    return pipe


def run_simulation():
    pipe = build_pipeline(dt=DT)
    if not pipe.compile():
        return
    dash: DashboardPlotter = pipe.runtime.nodes["Dashboard"]  # type: ignore[assignment]
    step_idx = 0
    while step_idx < 3000:
        if dash.paused:
            dash.render_static()
            continue
        pipe.run(ticks=1, dt=DT)
        plant_node = pipe.runtime.nodes["Plant"]
        state = pipe.runtime.port_state.get(plant_node.outputs["plant_state"], {"theta": 0.0, "omega": 0.0})
        control = pipe.runtime.port_state.get(pipe.runtime.nodes["MPC"].outputs["force"], 0.0)
        print(f"T={step_idx*DT:.2f} theta={state['theta']:.3f} omega={state['omega']:.3f} control={control:.3f}")
        step_idx += 1


if __name__ == "__main__":
    run_simulation()
