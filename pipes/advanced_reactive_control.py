from __future__ import annotations

from rg_compiler.api import Pipeline
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.dsl import Expr, Delay
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.contracts import contract
from rg_compiler.core.variables import SumPolicy


class Setpoint(CoreNode):
    value = Output[float]()

    @reaction
    def emit(self) -> Expr[float]:
        return 1.0


class Plant(CoreNode):
    control = Input[float](default=0.0)
    temp = State[float](init=20.0)
    reading = Output[float]()

    @reaction(rank="temp", max_microsteps=8)
    def step(self, control: Expr[float], temp: Expr[float]) -> Expr[float]:
        next_temp = temp + 0.1 * (control - temp)
        self.temp.set(next_temp)
        return Delay(next_temp, 20.0)


class Sensor(CoreNode):
    raw = Input[float](default=20.0)
    filtered = Output[float]()

    @reaction
    def smooth(self, raw: Expr[float]) -> Expr[float]:
        return (raw + Delay(raw, 20.0)) * 0.5


class PIDController(CoreNode):
    setpoint = Input[float](default=0.0)
    measured = Input[float](default=0.0)
    integral = State[float](init=0.0, policy=SumPolicy())
    control = Output[float]()

    @reaction(rank="integral", max_microsteps=16)
    def regulate(self, setpoint: Expr[float], measured: Expr[float], integral: Expr[float]) -> Expr[float]:
        error = setpoint - measured
        self.integral.set(integral + error)
        return 0.5 * error + 0.1 * integral


class Telemetry(ExtNode):
    value = Input[float]()
    control = Input[float]()

    @contract(no_instant_loop=True, deterministic=True)
    def step(self, ctx):
        # Pure side-effect placeholder; contract marks this as a loop breaker.
        ctx.read(self.inputs["value"])
        ctx.read(self.inputs["control"])


class EventCounter(CoreNode):
    spike = Input[float](default=0.0)
    counts = State[float](init=0.0, policy=SumPolicy())
    out = Output[float]()

    @reaction(rank="counts", max_microsteps=8)
    def track(self, spike: Expr[float], counts: Expr[float]) -> Expr[float]:
        self.counts.set(counts + spike)
        return counts


def build_pipeline() -> Pipeline:
    pipe = Pipeline(mode="strict")

    sp = Setpoint("sp")
    plant = Plant("plant")
    sensor = Sensor("sensor")
    pid = PIDController("pid")
    telem = Telemetry("telem")
    counter = EventCounter("counter")

    pipe.add(sp, plant, sensor, pid, telem, counter)

    sp.o.value >> pid.i.setpoint
    plant.o.reading >> sensor.i.raw
    sensor.o.filtered >> pid.i.measured
    pid.o.control >> plant.i.control

    sensor.o.filtered >> telem.i.value
    pid.o.control >> telem.i.control

    sensor.o.filtered >> counter.i.spike

    return pipe


if __name__ == "__main__":
    pipeline = build_pipeline()
    pipeline.run(ticks=5)
