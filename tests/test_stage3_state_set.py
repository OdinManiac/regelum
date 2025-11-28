import pytest
from rg_compiler.core.dsl import Expr
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.runtime import GraphRuntime

class ExplicitStateNode(CoreNode):
    val = State[int](init=10)
    out = Output[int]()
    
    @reaction
    def update(self, val: Expr[int]) -> Expr[int]:
        self.val.set(val + 5)
        return val

def test_explicit_state_update():
    runtime = GraphRuntime()
    node = ExplicitStateNode("N1")
    runtime.add_node(node)
    runtime.build_schedule()
    
    runtime.run_tick()
    assert runtime.port_state[node.outputs["out"]] == 10
    assert runtime.var_state["N1.val"] == 15
    
    runtime.run_tick()
    assert runtime.port_state[node.outputs["out"]] == 15
    assert runtime.var_state["N1.val"] == 20

def test_multiple_instances_state_isolation():
    pass

class InputStateNode(CoreNode):
    inp = Input[int]()
    val = State[int](init=0)
    
    @reaction
    def process(self, inp: Expr[int], val: Expr[int]) -> Expr[int]:
        self.val.set(val + inp)
        return val

def test_state_isolation():
    runtime = GraphRuntime()
    n1 = InputStateNode("N1")
    n2 = InputStateNode("N2")
    runtime.add_node(n1)
    runtime.add_node(n2)
    runtime.build_schedule()
    
    runtime.run_tick(inputs={
        n1.inputs["inp"]: 10,
        n2.inputs["inp"]: 20
    })
    
    assert runtime.var_state["N1.val"] == 10
    assert runtime.var_state["N2.val"] == 20
