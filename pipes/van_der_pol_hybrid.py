from __future__ import annotations

import math
from collections import deque
from typing import Sequence

import numpy as np
import pygame

from rg_compiler.api import Pipeline
from rg_compiler.core.continuous import ContinuousNode, ContinuousState
from rg_compiler.core.contracts import contract
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Delay, Expr
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.node import Context
from rg_compiler.vis import DashboardPlotter, DashboardSignal, build_dashboard

DT = 0.01
MU = 1.2
TARGET_X = 1.0
TARGET_Y = 0.0
KP = 5.5
KD = 4.0
U_LIMIT = 5.0
PHASE_RANGE = 3.0
MAX_TRAIL = 800
CONTROL_COLOR = (255, 99, 71)
STATE_COLOR = (54, 162, 235)
BASELINE_COLOR = (90, 200, 255)


def _compute_field(mu: float) -> list[tuple[float, float, float, float]]:
    grid = np.linspace(-PHASE_RANGE, PHASE_RANGE, 13)
    field: list[tuple[float, float, float, float]] = []
    for x in grid:
        for y in grid:
            dx = y
            dy = mu * (1.0 - x * x) * y - x
            norm = math.hypot(dx, dy)
            if norm > 1e-9:
                dx /= norm
                dy /= norm
            field.append((float(x), float(y), float(dx), float(dy)))
    return field


def make_draw_vdp_phase(mu: float):
    phase_field = _compute_field(mu)
    phase_trail: deque[tuple[float, float]] = deque(maxlen=MAX_TRAIL)
    phase_trail_free: deque[tuple[float, float]] = deque(maxlen=MAX_TRAIL)

    def draw(surface: pygame.Surface, rect: pygame.Rect, values: dict[str, float], t: float) -> None:
        x_val = float(values.get("x", 0.0))
        y_val = float(values.get("y", 0.0))
        x_free = float(values.get("x_free", 0.0))
        y_free = float(values.get("y_free", 0.0))
        phase_trail.append((x_val, y_val))
        phase_trail_free.append((x_free, y_free))

        def to_px(x: float, y: float) -> tuple[int, int]:
            sx = rect.left + rect.width // 2
            sy = rect.top + rect.height // 2
            scale = 0.5 * min(rect.width, rect.height) / PHASE_RANGE
            px = int(sx + x * scale)
            py = int(sy - y * scale)
            return px, py

        surface.fill((12, 12, 16), rect)
        pygame.draw.rect(surface, (30, 30, 38), rect, width=1)

        # Axes
        pygame.draw.line(surface, (70, 70, 85), (rect.left, rect.centery), (rect.right, rect.centery), 1)
        pygame.draw.line(surface, (70, 70, 85), (rect.centerx, rect.top), (rect.centerx, rect.bottom), 1)

        for x, y, dx, dy in phase_field:
            start = to_px(x, y)
            end = to_px(x + dx * 0.3, y + dy * 0.3)
            pygame.draw.line(surface, (80, 90, 120), start, end, 1)

        if len(phase_trail_free) > 1:
            free_points = [to_px(x, y) for x, y in phase_trail_free]
            pygame.draw.lines(surface, BASELINE_COLOR, False, free_points, 1)

        if len(phase_trail) > 1:
            points = [to_px(x, y) for x, y in phase_trail]
            pygame.draw.lines(surface, CONTROL_COLOR, False, points, 2)

        px, py = to_px(x_val, y_val)
        pygame.draw.circle(surface, CONTROL_COLOR, (px, py), 6)
        px_free, py_free = to_px(x_free, y_free)
        pygame.draw.circle(surface, BASELINE_COLOR, (px_free, py_free), 5)

        font = pygame.font.SysFont("monospace", 14)
        label = font.render("Phase portrait (x vs y)", True, (220, 220, 230))
        surface.blit(label, (rect.left + 8, rect.top + 6))
        legend_font = pygame.font.SysFont("monospace", 12)
        legend_control = legend_font.render("controlled", True, CONTROL_COLOR)
        legend_free = legend_font.render("free (u=0)", True, BASELINE_COLOR)
        surface.blit(legend_control, (rect.left + 8, rect.top + 22))
        surface.blit(legend_free, (rect.left + 8, rect.top + 36))

    return draw


class VanDerPolContinuous(ContinuousNode):
    max_step = DT
    x = ContinuousState(1.0)
    y = ContinuousState(0.0)
    mu: float = MU

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        x_val = state["x"]
        y_val = state["y"]
        u = inputs.get("u", 0.0)
        dx = y_val
        dy = self.mu * (1.0 - x_val * x_val) * y_val - x_val + u
        return {"x": dx, "y": dy}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"], "y": state["y"]}


class StateDelay(CoreNode):
    plant_state = Input[dict](default={"x": 1.0, "y": 0.0})
    plant_state_delayed = Output[dict]()

    @reaction
    def feed(self, plant_state: Expr[dict]) -> Expr[dict]:
        return Delay(plant_state, default={"x": 1.0, "y": 0.0})


class Splitter(ExtNode):
    plant_state_delayed = Input[dict](default={"x": 1.0, "y": 0.0})
    x = Output[float]()
    y = Output[float]()

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        state = ctx.read(self.inputs["plant_state_delayed"])
        ctx.write(self.outputs["x"], state.get("x", 0.0))
        ctx.write(self.outputs["y"], state.get("y", 0.0))


class BaselineSplitter(ExtNode):
    plant_state_free = Input[dict](default={"x": 1.0, "y": 0.0})
    x_free = Output[float]()
    y_free = Output[float]()

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        state = ctx.read(self.inputs["plant_state_free"])
        ctx.write(self.outputs["x_free"], state.get("x", 0.0))
        ctx.write(self.outputs["y_free"], state.get("y", 0.0))


class PDController(ExtNode):
    x = Input[float](default=TARGET_X)
    y = Input[float](default=0.0)
    u_cmd = Output[float]()

    def __init__(
        self,
        node_id: str,
        *,
        target_x: float | None = None,
        kp: float | None = None,
        kd: float | None = None,
        u_limit: float | None = None,
    ):
        super().__init__(node_id)
        self._target_x = TARGET_X if target_x is None else target_x
        self._kp = KP if kp is None else kp
        self._kd = KD if kd is None else kd
        self._u_limit = U_LIMIT if u_limit is None else u_limit

    @contract(deterministic=True, no_side_effects=True, no_instant_loop=False)
    def step(self, ctx: Context):
        x_val = ctx.read(self.inputs["x"])
        y_val = ctx.read(self.inputs["y"])
        control = self._kp * (self._target_x - x_val) - self._kd * y_val
        if control > self._u_limit:
            bounded = self._u_limit
        elif control < -self._u_limit:
            bounded = -self._u_limit
        else:
            bounded = control
        ctx.write(self.outputs["u_cmd"], bounded)

class ControlDelay(CoreNode):
    u_cmd = Input[float](default=0.0)
    u = Output[float]()

    @reaction
    def feed(self, u_cmd: Expr[float]) -> Expr[float]:
        return Delay(u_cmd, default=0.0)


class StateSink(ExtNode):
    plant_state = Input[dict](default={"x": 1.0, "y": 0.0})

    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.last: dict[str, float] | None = None

    @contract(deterministic=True, no_side_effects=False, no_instant_loop=False)
    def step(self, ctx: Context):
        self.last = ctx.read(self.inputs["plant_state"])


def build_pipeline(
    dt: float = DT,
    *,
    mu: float = MU,
    target: float | None = None,
    target_x: float | None = None,
    kp: float | None = None,
    kd: float | None = None,
    u_limit: float | None = None,
) -> tuple[Pipeline, StateSink]:
    resolved_target = target_x if target_x is not None else target

    plant = VanDerPolContinuous("Plant").as_hybrid(
        dt=dt,
        hold_init=0.0,
        u_name="u",
        y_name="plant_state",
        state_name="continuous_state",
    )
    plant_free = VanDerPolContinuous("PlantFree").as_hybrid(
        dt=dt,
        hold_init=0.0,
        u_name="u_free",
        y_name="plant_state_free",
        state_name="continuous_state_free",
    )
    plant.inner.mu = mu  # type: ignore[attr-defined]
    plant_free.inner.mu = mu  # type: ignore[attr-defined]
    delay = StateDelay("Delay")
    split = Splitter("Split")
    split_free = BaselineSplitter("SplitFree")
    ctrl = PDController("Controller", target_x=resolved_target, kp=kp, kd=kd, u_limit=u_limit)
    u_delay = ControlDelay("ControlDelay")
    sink = StateSink("Sink")

    dash = build_dashboard(
        "Dashboard",
        [
            DashboardSignal("x", "x", CONTROL_COLOR),
            DashboardSignal("y", "y", STATE_COLOR),
            DashboardSignal("u", "u", (153, 102, 255)),
            DashboardSignal("x_free", "x_free", BASELINE_COLOR),
            DashboardSignal("y_free", "y_free", BASELINE_COLOR),
        ],
        time_window=None,
        custom_anim=make_draw_vdp_phase(mu),
    )

    pipe = Pipeline(mode="strict")
    pipe.add(plant, plant_free, delay, split, split_free, ctrl, u_delay, sink, dash)
    pipe.auto_wire(strict=True)
    return pipe, sink


def run_simulation():
    pipe, _ = build_pipeline(dt=DT, target_x=1.0, kp=15.5, kd=0.8, u_limit=3.0)
    if not pipe.compile():
        return
    dash: DashboardPlotter = pipe.runtime.nodes["Dashboard"]  # type: ignore[assignment]
    step_idx = 0
    while step_idx < 3010:
        if dash.paused:
            dash.render_static()
            continue
        pipe.run(ticks=1, dt=DT)
        plant_node = pipe.runtime.nodes["Plant"]
        state = pipe.runtime.port_state.get(plant_node.outputs["plant_state"], {"x": 0.0, "y": 0.0})
        baseline_state = pipe.runtime.port_state.get(
            pipe.runtime.nodes["PlantFree"].outputs["plant_state_free"], {"x": 0.0, "y": 0.0}
        )
        control = pipe.runtime.port_state.get(pipe.runtime.nodes["ControlDelay"].outputs["u"], 0.0)
        if step_idx % 50 == 0:
            print(
                f"T={step_idx*DT:.2f} "
                f"x={state['x']:.3f} y={state['y']:.3f} "
                f"x_free={baseline_state['x']:.3f} y_free={baseline_state['y']:.3f} "
                f"u={control:.3f}"
            )
        step_idx += 1


if __name__ == "__main__":
    run_simulation()
