import math
from dataclasses import dataclass
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.dsl import Expr, If, Const, Var, BinOp, Cmp
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import StructuralPass, TypeCheckPass, CausalityPass, WriteConflictPass
from rg_compiler.compiler.report import CompilationReport
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.contracts import contract
from rg_compiler.core.node import Context
from rg_compiler.core.values import is_absent
from rg_compiler.api import Pipeline 

DT = 0.01
GRAVITY = 9.81
LENGTH = 1.0
MASS = 1.0
TARGET_ANGLE = 3.14159 

# Unified Port Names for Auto-Wiring:
# force, state, theta, omega

class PendulumPhysics(ExtNode):
    # Use defaults to handle Absent values automatically
    force = Input[float](default=0.0)
    state = Input[tuple[float, float]](default=(0.1, 0.0))
    next_state = Output[tuple[float, float]]() 

    @contract(deterministic=True, no_side_effects=True)
    def step(self, ctx: Context):
        # Runtime automatically substitutes default if Absent
        force = ctx.read(self.inputs["force"])
        state = ctx.read(self.inputs["state"])
        
        theta, omega = state
        alpha = - (GRAVITY / LENGTH) * math.sin(theta) + force
        
        new_omega = omega + alpha * DT
        new_theta = theta + new_omega * DT
        
        ctx.write(self.outputs["next_state"], (new_theta, new_omega))

class PIDController(CoreNode):
    theta = Input[float]()
    omega = Input[float]()
    force = Output[float]()
    
    integral = State[float](init=0.0)
    
    Kp = 10.0
    Ki = 0.1
    Kd = 1.0
    
    @reaction
    def control(self, theta: Expr[float], omega: Expr[float], integ: Expr[float]) -> Expr[float]:
        error = 3.14159 - theta
        p_term = error * self.Kp
        new_integ = integ + error * DT
        i_term = new_integ * self.Ki
        self.integral.set(new_integ)
        d_term = (0.0 - omega) * self.Kd
        u = p_term + i_term + d_term
        return u

class Splitter(ExtNode):
    state = Input[tuple[float, float]]()
    theta = Output[float]()
    omega = Output[float]()
    
    @contract(deterministic=True)
    def step(self, ctx: Context):
        # No default here? If absent, we just don't write.
        # But wait, ctx.read returns ABSENT if no default.
        s = ctx.read(self.inputs["state"])
        
        if not is_absent(s):
            t, o = s
            ctx.write(self.outputs["theta"], t)
            ctx.write(self.outputs["omega"], o)

class SharedStorage:
    def __init__(self, val):
        self.val = val

class DelayOut(ExtNode):
    state = Output[tuple[float, float]]() 
    
    def __init__(self, node_id: str, storage: SharedStorage):
        super().__init__(node_id)
        self.storage = storage
        
    @contract(deterministic=True, no_instant_loop=True)
    def step(self, ctx: Context):
        ctx.write(self.outputs["state"], self.storage.val)

class DelayIn(ExtNode):
    next_state = Input[tuple[float, float]]() 
    
    def __init__(self, node_id: str, storage: SharedStorage):
        super().__init__(node_id)
        self.storage = storage
        
    @contract(deterministic=True)
    def step(self, ctx: Context):
        curr = ctx.read(self.inputs["next_state"])
        if not is_absent(curr):
            self.storage.val = curr

def run_simulation():
    pipe = Pipeline(mode="pragmatic")
    
    phys = PendulumPhysics("Physics")
    ctrl = PIDController("PID")
    split = Splitter("Split")
    
    delay_storage = SharedStorage((0.1, 0.0))
    delay_out = DelayOut("DelayOut", delay_storage)
    delay_in = DelayIn("DelayIn", delay_storage)
    
    pipe.add(phys, ctrl, delay_out, delay_in, split)
    
    print("Auto-wiring...")
    pipe.auto_wire(strict=True)
    
    if not pipe.compile():
        return

    print("Starting Simulation...")
    for i in range(100):
        pipe.runtime.run_tick()
        t = i * DT
        theta = delay_storage.val[0]
        print(f"T={t:.2f} Theta={theta:.4f}")

if __name__ == "__main__":
    run_simulation()
