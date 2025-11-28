import pytest
from rg_compiler.core.dsl import Expr
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.variables import LWWPolicy

class FixedTestNode(CoreNode):
    x = Input[int]()
    out = Output[int]()
    
    @reaction
    def process(self, x: Expr[int]) -> Expr[int]:
        return x + 10

def test_fixed_core_node():
    runtime = GraphRuntime()
    node = FixedTestNode("N1")
    runtime.add_node(node)
    
    runtime.build_schedule()
    # Inject input via run_tick arg
    runtime.run_tick(inputs={node.inputs["x"]: 20})
    
    assert runtime.port_state[node.outputs["out"]] == 30

class StateNode(CoreNode):
    val = State[int](init=5)
    out = Output[int]()
    
    @reaction
    def step_state(self, val: Expr[int]) -> Expr[int]:
        return val + 1

def test_state_node():
    runtime = GraphRuntime()
    node = StateNode("N2")
    runtime.add_node(node)
    
    runtime.build_schedule()
    runtime.run_tick()
    
    # Expect output = 5 + 1 = 6
    assert runtime.port_state[node.outputs["out"]] == 6
