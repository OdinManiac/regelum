import pytest
from regelum.core.node import Context, IntentContext
from regelum.core.core_node import CoreNode, Input, Output, State, reaction
from regelum.core.ext_node import ExtNode
from regelum.core.dsl import Expr, if_, const, var, Delay
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig
from regelum.compiler.passes import StructuralPass, TypeCheckPass, WriteConflictPass, CausalityPass
from regelum.compiler.passes_sdf import SDFPass
from regelum.core.variables import SumPolicy

# --- Helpers ---
def run_compile(runtime: GraphRuntime):
    config = CompilerConfig(mode="strict")
    pipeline = CompilerPipeline(config)
    pipeline.add_pass(StructuralPass())
    pipeline.add_pass(TypeCheckPass())
    pipeline.add_pass(WriteConflictPass())
    pipeline.add_pass(CausalityPass())
    pipeline.add_pass(SDFPass())
    
    ir = pipeline.build_ir(runtime)
    return pipeline.run_passes(ir)

# --- Scenario 2: Cycle involving ExtNode (CAUS001) ---
def test_scenario_2_causality_ext_node():
    class AccountNode2(CoreNode):
        val = State(0)
        upd = Input(default=0)
        out = Output()
        
        @reaction
        def loop(self, upd):
            self.val.set(upd)
            
    class Strategy(ExtNode):
        acc = Input()
        act = Output()
        def step(self, ctx):
            v = ctx.read(self.inputs['acc'])
            ctx.write(self.outputs['act'], v)

    class Market(ExtNode):
        act = Input()
        acc_out = Output()
        def step(self, ctx):
            a = ctx.read(self.inputs['act'])
            ctx.write(self.outputs['acc_out'], a)
            
    runtime = GraphRuntime()
    acc = AccountNode2("acc")
    strat = Strategy("strat")
    market = Market("market")
    
    runtime.add_node(acc)
    runtime.add_node(strat)
    runtime.add_node(market)
    
    runtime.connect(strat.o.act, market.i.act)
    runtime.connect(market.o.acc_out, strat.i.acc)
    
    res = run_compile(runtime)
    
    # Should fail with CAUS001 because ExtNodes are in cycle
    assert not res.success
    errors = [d.code for d in res.diagnostics if d.severity.name == "ERROR"]
    assert "CAUS001" in errors

# --- Scenario 3: Multiple Writers (WRITE001) ---
def test_scenario_3_multiple_writers():
    class MultiWriteNode(CoreNode):
        val = State(0)  # Default ErrorPolicy
        in1 = Input(default=0)
        in2 = Input(default=0)
        
        @reaction
        def write1(self, in1):
            self.val.set(in1)
            
        @reaction
        def write2(self, in2):
            self.val.set(in2)
            
    runtime = GraphRuntime()
    node = MultiWriteNode("node")
    runtime.add_node(node)
    
    res = run_compile(runtime)
    
    # ErrorPolicy forbids multi-writers, should fail deterministically.
    assert not res.success
    errors = [d.code for d in res.diagnostics if d.severity.name == "ERROR"]
    assert "WRITE001" in errors

def test_scenario_3_sum_policy_allows_merge():
    class SumNode(CoreNode):
        total = State(0, policy=SumPolicy())
        in_a = Input(default=0)
        in_b = Input(default=0)
        out = Output()
        
        @reaction
        def write_a(self, in_a: Expr[int]) -> Expr[int]:
            self.total.set(in_a)
            return in_a
        
        @reaction
        def write_b(self, in_b: Expr[int]) -> Expr[int]:
            self.total.set(in_b)
            return in_b
    
    runtime = GraphRuntime()
    node = SumNode("sum")
    runtime.add_node(node)
    
    res = run_compile(runtime)
    assert res.success

# --- Scenario 4: Constructive Cycle (CAUS003) ---
def test_scenario_4_constructive_cycle():
    class LoopNode(CoreNode):
        in_ = Input(default=0)
        out = Output()
        
        @reaction
        def tick(self, in_):
            return 1 - in_
            
    runtime = GraphRuntime()
    node = LoopNode("loop")
    runtime.add_node(node)
    
    # Connect output to input -> Self loop x=x
    runtime.connect(node.o.out, node.i.in_)
    
    res = run_compile(runtime)
    
    # x = x is non-constructive (unknown -> unknown)
    # Expect CAUS003 or CAUS002.
    # CausalityPass: if len(scc) == 1: CAUS002.
    # But if it's CoreNode and self-loop, we might want CAUS003 if constructive check fails.
    # My updated logic checks constructive for scc=1 if it's Reaction self-loop.
    # Here: R:step writes Out. Out connected to In. R:step reads In.
    # So R:step depends on R:step.
    # It is a self-loop in reaction dependency graph.
    # So CAUS002 or CAUS003.
    # The implementation uses CAUS003 if constructive check fails for self-loop.
    
    assert not res.success
    errors = [d.code for d in res.diagnostics if d.severity.name == "ERROR"]
    assert "CAUS003" in errors or "CAUS002" in errors

def test_scenario_4_constructive_cycle_fixed_with_delay():
    class DelayNode(CoreNode):
        in_ = Input(default=0)
        out = Output()
        
        @reaction
        def tick(self, in_):
            # Using Delay(in_, default=0)
            return Delay(in_, 0)
            
    runtime = GraphRuntime()
    node = DelayNode("delay_loop")
    runtime.add_node(node)
    
    runtime.connect(node.o.out, node.i.in_)
    
    res = run_compile(runtime)
    
    # Delay breaks the instant cycle. Should pass.
    # CAUS002/003 should NOT be present.
    
    errors = [d.code for d in res.diagnostics if d.severity.name == "ERROR"]
    assert "CAUS002" not in errors
    assert "CAUS003" not in errors
    assert res.success

# --- Scenario 5: SDF Rate Mismatch (SDF001) ---
def test_scenario_5_sdf_mismatch():
    class Producer(ExtNode):
        out = Output(rate=1)
        def step(self, ctx): pass
        
    class Consumer(ExtNode):
        in_ = Input(rate=32)
        def step(self, ctx): pass
        
    runtime = GraphRuntime()
    prod = Producer("prod")
    cons = Consumer("cons")
    runtime.add_node(prod)
    runtime.add_node(cons)
    
    runtime.connect(prod.o.out, cons.i.in_)
    
    res = run_compile(runtime)
    
    # Should warn SDF001
    warnings = [d.code for d in res.diagnostics if d.severity.name == "WARNING"]
    assert "SDF001" in warnings

if __name__ == "__main__":
    pytest.main([__file__])
