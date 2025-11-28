import math
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import StructuralPass, TypeCheckPass, CausalityPass, WriteConflictPass
from rg_compiler.compiler.passes_sdf import SDFPass
from rg_compiler.compiler.report import CompilationReport

# Example: Audio Downsampling Pipeline
# Source (44100 Hz) -> Filter -> Downsampler (M=4) -> Sink (11025 Hz)
# SDF Rates: Source(1) -> Filter(1) -> Down(4 in, 1 out) -> Sink(1)
# Note: In our normalized model, if Source produces N, Filter consumes N.
# Here we model rates per firing.
# Source produces 4 samples per firing? Or 1?
# Standard SDF:
# Source: out=4
# Filter: in=4, out=4
# Downsample: in=4, out=1
# Sink: in=1
# This is consistent.

class AudioSource(CoreNode):
    # Simulating a block source
    audio_out = Output[float](rate=4) 
    
    counter = State[int](init=0)
    
    @reaction
    def produce(self, c: Expr[int]) -> Expr[int]:
        self.counter.set(c + 1)
        # In real SDF, we would output a list of 4 samples.
        # Current Core DSL handles scalar Expr.
        # SDFPass checks metadata consistency only.
        return 1 # Placeholder for data

class LowPassFilter(CoreNode):
    audio_in = Input[float](rate=4)
    audio_out = Output[float](rate=4)
    
    @reaction
    def process(self, inp: Expr[float]) -> Expr[float]:
        # DSP logic
        return inp 

class Downsampler(CoreNode):
    # Consumes 4, produces 1
    sig_in = Input[float](rate=4)
    sig_out = Output[float](rate=1)
    
    @reaction
    def decimate(self, inp: Expr[float]) -> Expr[float]:
        # Logic to pick 1 out of 4
        return inp

class AudioSink(CoreNode):
    sig_in = Input[float](rate=1)
    
    @reaction
    def play(self, val: Expr[float]):
        pass

def run_multirate_check():
    runtime = GraphRuntime()
    
    src = AudioSource("src")
    lpf = LowPassFilter("lpf")
    down = Downsampler("down")
    sink = AudioSink("sink")
    
    runtime.add_node(src)
    runtime.add_node(lpf)
    runtime.add_node(down)
    runtime.add_node(sink)
    
    # Wiring
    src.o.audio_out >> lpf.i.audio_in
    lpf.o.audio_out >> down.i.sig_in
    down.o.sig_out >> sink.i.sig_in
    
    # Compile with SDF check
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(StructuralPass())
    compiler.add_pass(SDFPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    print(CompilationReport(ir, res.diagnostics))
    
    if res.success:
        print("SDF Graph is Consistent!")
    else:
        print("SDF Consistency Failed!")

def run_inconsistent_check():
    print("\n--- Inconsistent Topology Check ---")
    runtime = GraphRuntime()
    # Source(4) -> Sink(1) directly?
    # 4 != 1. Should fail consistency unless they fire at different rates 1:4.
    # Our SDFPass checks q_src * prod = q_dst * cons.
    # q_src * 4 = q_dst * 1.
    # q_src = 1, q_dst = 4.
    # This IS consistent if nodes fire 1 vs 4 times.
    # Wait, SDFPass logic:
    # "If v in q_comp ... check if q_comp[v] matches expected".
    # If graph is linear Source -> Sink, it is ALWAYS consistent (q propagates).
    # Inconsistency only happens in CYCLES or reconvergent paths with mismatch.
    
    # Let's build a mismatch loop.
    # A(out:2) -> B(in:1, out:1) -> A(in:1)
    # q_a * 2 = q_b * 1
    # q_b * 1 = q_a * 1 => q_a = q_b
    # => 2 q_a = q_a => q_a = 0. Invalid.
    
    class NodeA(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=2)
        @reaction
        def step(self, i:Expr[int])->Expr[int]: return i
        
    class NodeB(CoreNode):
        inp = Input[int](rate=1)
        out = Output[int](rate=1)
        @reaction
        def step(self, i:Expr[int])->Expr[int]: return i
        
    a = NodeA("A")
    b = NodeB("B")
    runtime.add_node(a)
    runtime.add_node(b)
    
    a.o.out >> b.i.inp
    b.o.out >> a.i.inp
    
    compiler = CompilerPipeline(CompilerConfig(mode="strict"))
    compiler.add_pass(SDFPass())
    
    ir = compiler.build_ir(runtime)
    res = compiler.run_passes(ir)
    
    print(CompilationReport(ir, res.diagnostics))
    if not res.success:
        print("Caught expected inconsistency!")

if __name__ == "__main__":
    run_multirate_check()
    run_inconsistent_check()

