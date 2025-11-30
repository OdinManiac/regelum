from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Sequence, Tuple

import pygame
import numpy as np

from regelum.api import Pipeline
from regelum.core.continuous import ContinuousNode, ContinuousState
from regelum.core.contracts import contract
from regelum.core.core_node import CoreNode, Input, Output, reaction
from regelum.core.dsl import Delay, Expr
from regelum.core.ext_node import ExtNode
from regelum.core.node import Context
from regelum.vis import DashboardPlotter, DashboardSignal, build_dashboard

DT = 0.02
GOAL_X = 0.0
GOAL_Y = 0.0
GOAL_THETA = 0.0

MPC_HORIZON = 50
MPC_POS_WEIGHT = 5.0
MPC_THETA_WEIGHT = 3.0
MPC_V_WEIGHT = 0.0
MPC_W_WEIGHT = 0.0
MPC_HEADING_WEIGHT = 0.0
V_CANDIDATES: tuple[float, ...] = np.linspace(-1.0, 1.0, 41)
W_CANDIDATES: tuple[float, ...] = np.linspace(-2.5, 2.5, 41)
V_LIMIT = 1.5
W_LIMIT = 2.8


def draw_unicycle(surface: pygame.Surface, rect: pygame.Rect, values: dict[str, float], t: float) -> None:
    x = values.get("x", 0.0)
    y = values.get("y", 0.0)
    theta = values.get("theta", 0.0)
    scale = min(rect.width, rect.height) * 0.15
    origin_x = rect.left + rect.width // 2
    origin_y = rect.top + rect.height // 2

    pygame.draw.rect(surface, (14, 14, 18), rect)
    # Draw simple grid and axes.
    pygame.draw.line(surface, (60, 60, 70), (origin_x, rect.top), (origin_x, rect.bottom), 1)
    pygame.draw.line(surface, (60, 60, 70), (rect.left, origin_y), (rect.right, origin_y), 1)

    # Vehicle pose.
    pos_x = origin_x + int(x * scale)
    pos_y = origin_y - int(y * scale)
    heading_len = int(scale * 0.8)
    heading_x = pos_x + int(math.cos(theta) * heading_len)
    heading_y = pos_y - int(math.sin(theta) * heading_len)
    radius = max(6, heading_len // 6)

    pygame.draw.circle(surface, (80, 190, 255), (pos_x, pos_y), radius)
    pygame.draw.line(surface, (255, 160, 90), (pos_x, pos_y), (heading_x, heading_y), 3)


class UnicycleContinuous(ContinuousNode):
    max_step = DT
    x = ContinuousState(1.0)
    y = ContinuousState(1.0)
    theta = ContinuousState(0.0)

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        v = inputs.get("v", 0.0)
        w = inputs.get("w", 0.0)
        return {
            "x": v * math.cos(state["theta"]),
            "y": v * math.sin(state["theta"]),
            "theta": w,
        }

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"], "y": state["y"], "theta": state["theta"]}


class StateDelay(CoreNode):
    plant_state = Input[dict](default={"x": 1.0, "y": 1.0, "theta": 0.0})
    plant_state_delayed = Output[dict]()

    @reaction
    def feed(self, plant_state: Expr[dict]) -> Expr[dict]:
        return Delay(plant_state, default={"x": 1.0, "y": 1.0, "theta": 0.0})


class Splitter(ExtNode):
    plant_state_delayed = Input[dict](default={"x": 1.0, "y": 1.0, "theta": 0.0})
    x = Output[float]()
    y = Output[float]()
    theta = Output[float]()

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        state = ctx.read(self.inputs["plant_state_delayed"])
        ctx.write(self.outputs["x"], state.get("x", 0.0))
        ctx.write(self.outputs["y"], state.get("y", 0.0))
        ctx.write(self.outputs["theta"], state.get("theta", 0.0))


class NonholonomicMPC(ExtNode):
    x = Input[float]()
    y = Input[float]()
    theta = Input[float]()
    v = Output[float]()
    w = Output[float]()
    u = Output[dict]()

    def __init__(
        self,
        node_id: str,
        *,
        horizon: int = MPC_HORIZON,
        dt: float = DT,
        v_candidates: Sequence[float] | None = None,
        w_candidates: Sequence[float] | None = None,
        pos_weight: float = MPC_POS_WEIGHT,
        theta_weight: float = MPC_THETA_WEIGHT,
        v_weight: float = MPC_V_WEIGHT,
        w_weight: float = MPC_W_WEIGHT,
        heading_weight: float = MPC_HEADING_WEIGHT,
        v_limit: float = V_LIMIT,
        w_limit: float = W_LIMIT,
    ):
        super().__init__(node_id)
        self._horizon = horizon
        self._dt = dt
        self._pos_weight = pos_weight
        self._theta_weight = theta_weight
        self._v_weight = v_weight
        self._w_weight = w_weight
        self._heading_weight = heading_weight
        self._v_limit = v_limit
        self._w_limit = w_limit
        v_array = np.asarray(v_candidates if v_candidates is not None else V_CANDIDATES, dtype=float)
        w_array = np.asarray(w_candidates if w_candidates is not None else W_CANDIDATES, dtype=float)
        self._v_grid, self._w_grid = np.meshgrid(
            np.clip(v_array, -self._v_limit, self._v_limit),
            np.clip(w_array, -self._w_limit, self._w_limit),
            indexing="ij",
        )

    def _simulate(self, x: float, y: float, theta: float, v: float, w: float) -> Tuple[float, float, float]:
        next_x = x + v * math.cos(theta) * self._dt
        next_y = y + v * math.sin(theta) * self._dt
        next_theta = theta + w * self._dt
        return next_x, next_y, next_theta

    def _evaluate_costs(self, x: float, y: float, theta: float) -> np.ndarray:
        v_grid = self._v_grid
        w_grid = self._w_grid
        x_grid = np.full_like(v_grid, x, dtype=float)
        y_grid = np.full_like(v_grid, y, dtype=float)
        theta_grid = np.full_like(v_grid, theta, dtype=float)
        cost = np.zeros_like(v_grid, dtype=float)

        for _ in range(self._horizon):
            x_grid = x_grid + v_grid * np.cos(theta_grid) * self._dt
            y_grid = y_grid + v_grid * np.sin(theta_grid) * self._dt
            theta_grid = theta_grid + w_grid * self._dt

            dx = x_grid - GOAL_X
            dy = y_grid - GOAL_Y
            dtheta = theta_grid - GOAL_THETA
            dist_sq = dx * dx + 5 * dy * dy
            desired_heading = np.where(dist_sq > 1e-9, np.arctan2(dy, dx), 0.0)
            heading_err = theta_grid - desired_heading

            cost = cost + (
                self._pos_weight * dist_sq
                + self._theta_weight * dtheta * dtheta
                + self._heading_weight * heading_err * heading_err
                + self._v_weight * v_grid * v_grid
                + self._w_weight * w_grid * w_grid
            )

        return cost

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        x_val = ctx.read(self.inputs["x"])
        y_val = ctx.read(self.inputs["y"])
        theta_val = ctx.read(self.inputs["theta"])

        cost_grid = self._evaluate_costs(x_val, y_val, theta_val)
        best_idx = np.unravel_index(np.argmin(cost_grid), cost_grid.shape)
        best_v = float(self._v_grid[best_idx])
        best_w = float(self._w_grid[best_idx])

        ctx.write(self.outputs["v"], best_v)
        ctx.write(self.outputs["w"], best_w)
        ctx.write(self.outputs["u"], {"v": best_v, "w": best_w})


def build_pipeline(dt: float = DT) -> Pipeline:
    plant = UnicycleContinuous("Plant").as_hybrid(
        dt=dt,
        hold_init=0.0,
        y_name="plant_state",
        state_name="continuous_state",
    )
    ctrl = NonholonomicMPC("MPC", dt=dt, horizon=MPC_HORIZON)
    delay = StateDelay("Delay")
    split = Splitter("Split")

    dash = build_dashboard(
        "Dashboard",
        [
            DashboardSignal("x", "x", (255, 99, 71)),
            DashboardSignal("y", "y", (54, 162, 235)),
            DashboardSignal("theta", "theta (rad)", (255, 205, 86)),
            DashboardSignal("v", "v", (75, 192, 192)),
            DashboardSignal("w", "w", (153, 102, 255)),
        ],
        time_window=None,
        custom_anim=draw_unicycle,
    )

    pipe = Pipeline(mode="strict")
    pipe.add(plant, ctrl, delay, split, dash)
    pipe.auto_wire(strict=True)
    return pipe


def run_simulation(record_dir: str | None = None):
    pipe = build_pipeline(dt=DT)
    if not pipe.compile():
        return
    dash: DashboardPlotter = pipe.runtime.nodes["Dashboard"]  # type: ignore[assignment]
    rec_dir = record_dir or os.getenv("REC_DIR")
    recorder = _make_recorder(rec_dir)

    step_idx = 0
    while step_idx < 2000:
        if dash.paused:
            dash.render_static()
            recorder()
            continue
        pipe.run(ticks=1, dt=DT)
        plant_node = pipe.runtime.nodes["Plant"]
        state = pipe.runtime.port_state.get(
            plant_node.outputs["plant_state"], {"x": 0.0, "y": 0.0, "theta": 0.0}
        )
        controls = pipe.runtime.port_state.get(pipe.runtime.nodes["MPC"].outputs["v"], 0.0), pipe.runtime.port_state.get(
            pipe.runtime.nodes["MPC"].outputs["w"], 0.0
        )
        print(
            f"T={step_idx*DT:.2f} x={state['x']:.3f} y={state['y']:.3f} theta={state['theta']:.3f} "
            f"v={controls[0]:.3f} w={controls[1]:.3f}"
        )
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
