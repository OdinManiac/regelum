from regelum.api import Pipeline
from regelum.core.core_node import CoreNode, Input, Output, State, reaction
from regelum.core.dsl import Expr
from regelum.compiler.report import CompilationReport

# Audio pipeline:
# Source (rate 4) -> LowPass (rate 4) -> Downsampler (4 -> 1) -> Sink (rate 1)
# This should pass SDF consistency checks.

class AudioSource(CoreNode):
    audio_out = Output[float](rate=4)
    counter = State[int](init=0)

    @reaction
    def produce(self, c: Expr[int]) -> Expr[int]:
        self.counter.set(c + 1)
        return c

class LowPassFilter(CoreNode):
    audio_in = Input[float](rate=4)
    audio_out = Output[float](rate=4)
    
    @reaction
    def process(self, inp: Expr[float]) -> Expr[float]:
        # DSP logic
        return inp 

class Downsampler(CoreNode):
    sig_in = Input[float](rate=4)
    sig_out = Output[float](rate=1)
    
    @reaction
    def decimate(self, inp: Expr[float]) -> Expr[float]:
        return inp

class AudioSink(CoreNode):
    sig_in = Input[float](rate=1)
    
    @reaction
    def play(self, val: Expr[float]):
        return val


def build_audio_pipeline() -> Pipeline:
    pipe = Pipeline(mode="strict")
    src = AudioSource("src")
    lpf = LowPassFilter("lpf")
    down = Downsampler("down")
    sink = AudioSink("sink")

    pipe.add(src, lpf, down, sink)
    src.o.audio_out >> lpf.i.audio_in
    lpf.o.audio_out >> down.i.sig_in
    down.o.sig_out >> sink.i.sig_in
    return pipe


def build_inconsistent_pipeline() -> Pipeline:
    pipe = Pipeline(mode="strict")

    class NodeA(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=2)

        @reaction
        def step(self, inp: Expr[int]) -> Expr[int]:
            return inp

    class NodeB(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=1)

        @reaction
        def step(self, inp: Expr[int]) -> Expr[int]:
            return inp

    a = NodeA("A")
    b = NodeB("B")
    pipe.add(a, b)
    a.o.out >> b.i.inp
    b.o.out >> a.i.inp
    return pipe


def run_multirate_check():
    pipe = build_audio_pipeline()
    if pipe.compile():
        print("SDF Graph is consistent")
    else:
        print(pipe.report)


def run_inconsistent_check():
    print("\n--- Inconsistent topology ---")
    pipe = build_inconsistent_pipeline()
    success = pipe.compile()
    print(pipe.report)
    if success:
        print("Expected SDF failure but compile succeeded!")
    else:
        print("Caught expected inconsistency.")


if __name__ == "__main__":
    run_multirate_check()
    run_inconsistent_check()