from __future__ import annotations

from rg_compiler.api import Pipeline
from rg_compiler.core.continuous import ContinuousNode, ContinuousState
from rg_compiler.core.hybrid_adapters import ContinuousWrapper, Sampler, ZeroOrderHold
from rg_compiler.core.node import Context, RawNode


class NonlinearPlant(ContinuousNode):
    """
    Нелинейное уравнение первого порядка с кубическим демпфером:
      x' = -a*x - b*x^3 + u
    """

    x = ContinuousState(1.0)
    a: float = 1.0
    b: float = 0.5

    def derivative(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        x_val = state["x"]
        u = inputs.get("u", 0.0)
        dx = -self.a * x_val - self.b * x_val * x_val * x_val + u
        return {"x": dx}

    def outputs(self, t: float, state: dict[str, float], inputs: dict[str, float]) -> dict[str, float]:
        return {"x": state["x"]}


class DiscretePController(RawNode):
    def __init__(self, node_id: str, target: float = 0.0, kp: float = 5.0):
        super().__init__(node_id)
        self.sample = self.add_input("sample", default={"x": 0.0})
        self.out = self.add_output("out")
        self.target = target
        self.kp = kp

    def step(self, ctx: Context) -> None:
        data = ctx.read(self.sample)
        x_val = data["x"]
        control = self.kp * (self.target - x_val)
        ctx.write(self.out, {"u": control})


class SampleSink(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.inp = self.add_input("inp")
        self.last = None

    def step(self, ctx: Context) -> None:
        self.last = ctx.read(self.inp)


def build_pipeline(dt: float = 0.01, target: float = 0.0) -> tuple[Pipeline, SampleSink]:
    """
    Гибридный пайплайн: дискретный P-контроллер -> ZOH -> нелинейный plant -> Sampler -> sink.
    """
    plant = NonlinearPlant("plant")
    plant.max_step = dt
    controller = DiscretePController("ctrl", target=target, kp=5.0)
    zoh = ZeroOrderHold("zoh", init={"u": 0.0})
    wrapper = ContinuousWrapper("wrap", plant, default_dt=dt)
    sampler = Sampler("sampler")
    sink = SampleSink("sink")

    pipe = Pipeline(mode="strict")
    pipe.add(controller, zoh, wrapper, sampler, sink)

    controller.o.out >> zoh.i.inp
    zoh.o.out >> wrapper.i.u
    wrapper.o.y >> sampler.i.inp
    sampler.o.out >> sink.i.inp

    return pipe, sink


if __name__ == "__main__":
    pipeline, sink = build_pipeline(dt=0.01, target=0.0)
    if pipeline.compile():
        pipeline.run(ticks=500, dt=0.01)
        print("Final state:", sink.last)
