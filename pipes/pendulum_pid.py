from __future__ import annotations

import math

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

    Kp = 5.0
    Ki = 0.0
    Kd = 5.0

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
    )

    pipe = Pipeline(mode="strict")
    pipe.add(plant, ctrl, split, delay, dash)
    pipe.auto_wire()

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
        control = pipe.runtime.port_state.get(pipe.runtime.nodes["PID"].outputs["force"], 0.0)
        print(f"T={step_idx*DT:.2f} theta={state['theta']:.3f} omega={state['omega']:.3f} control={control:.3f}")
        step_idx += 1


if __name__ == "__main__":
    run_simulation()
