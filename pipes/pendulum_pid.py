from __future__ import annotations

import math
import os
from pathlib import Path

import pygame

from regelum.api import Pipeline
from regelum.core.continuous import ContinuousNode, ContinuousState
from regelum.core.contracts import contract
from regelum.core.core_node import CoreNode, Input, Output, State, reaction
from regelum.core.dsl import Expr, Delay, If
from regelum.core.ext_node import ExtNode
from regelum.core.node import Context
from regelum.vis import DashboardPlotter, DashboardSignal, build_dashboard

DT = 0.01
GRAVITY = 9.81
LENGTH = 1.0
TARGET_ANGLE = math.pi
MAX_FORCE = 500.0


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


class PIDController(CoreNode):
    theta = Input[float]()
    omega = Input[float]()
    force = Output[float]()

    integral = State[float](init=0.0)

    Kp = 15.0
    Ki = 0.0
    Kd = 1.0

    @reaction(rank="integral", max_microsteps=2)
    def control(self, theta: Expr[float], omega: Expr[float], integral: Expr[float]) -> Expr[float]:
        error = TARGET_ANGLE - theta

        p_term = error * self.Kp
        new_integ = integral + error * DT
        clamped_integ = If(
            new_integ > MAX_FORCE,
            MAX_FORCE,
            If(new_integ < -MAX_FORCE, -MAX_FORCE, new_integ),
        )
        self.integral.set(clamped_integ)

        i_term = clamped_integ * self.Ki
        d_term = (0.0 - omega) * self.Kd
        u = p_term + i_term + d_term

        return If(u > MAX_FORCE, MAX_FORCE, If(u < -MAX_FORCE, -MAX_FORCE, u))


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


def build_pipeline(dt: float = DT) -> Pipeline:
    plant = PendulumContinuous("Plant").as_hybrid(
        dt=dt,
        hold_init=0.0,
        u_name="force",
        y_name="plant_state",
        state_name="continuous_state",
    )
    ctrl = PIDController("PID")
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
    pipe.auto_wire()

    return pipe


def run_simulation(record_dir: str | None = None):
    pipe = build_pipeline(dt=DT)
    if not pipe.compile():
        return
    dash: DashboardPlotter = pipe.runtime.nodes["Dashboard"]  # type: ignore[assignment]
    rec_dir = record_dir or os.getenv("REC_DIR")
    recorder = _make_recorder(rec_dir)

    step_idx = 0
    while step_idx < 3000:
        if dash.paused:
            dash.render_static()
            recorder()
            continue
        pipe.run(ticks=1, dt=DT)
        plant_node = pipe.runtime.nodes["Plant"]
        state = pipe.runtime.port_state.get(plant_node.outputs["plant_state"], {"theta": 0.0, "omega": 0.0})
        control = pipe.runtime.port_state.get(pipe.runtime.nodes["PID"].outputs["force"], 0.0)
        print(f"T={step_idx*DT:.2f} theta={state['theta']:.3f} omega={state['omega']:.3f} control={control:.3f}")
        step_idx += 1
        recorder()


def _make_recorder(rec_dir: str | None):
    if not rec_dir:
        return lambda: None
    base = Path(rec_dir)
    frame_idx = 0

    def capture():
        nonlocal frame_idx
        surf = pygame.display.get_surface()
        if surf is None:
            return
        base.mkdir(parents=True, exist_ok=True)
        pygame.image.save(surf, str(base / f"frame_{frame_idx:05d}.png"))
        frame_idx += 1

    return capture


if __name__ == "__main__":
    run_simulation()
