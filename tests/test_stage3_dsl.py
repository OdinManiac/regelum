import pytest
from rg_compiler.core.dsl import Expr, Delay
from rg_compiler.core.core_node import CoreNode, Input, Output, State, reaction
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.contracts import contract
from rg_compiler.core.node import Context
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

class DelayNode(CoreNode):
    x = Input[int](default=0)
    out = Output[int]()
    
    @reaction
    def use_delay(self, x: Expr[int]) -> Expr[int]:
        return Delay(x, default=-1)

def test_delay_outputs_previous_tick():
    runtime = GraphRuntime()
    node = DelayNode("Delay")
    runtime.add_node(node)
    
    runtime.build_schedule()
    
    runtime.run_tick(inputs={node.inputs["x"]: 5})
    assert runtime.port_state[node.outputs["out"]] == -1
    
    runtime.run_tick(inputs={node.inputs["x"]: 7})
    assert runtime.port_state[node.outputs["out"]] == 5


def test_delay_value_available_before_producer_runs():
    class DelayProducer(CoreNode):
        x = Input[int](default=0)
        out = Output[int]()

        @reaction
        def tick(self, x: Expr[int]) -> Expr[int]:
            return Delay(x, default=-1)

    class EarlyConsumer(ExtNode):
        inp = Input[int]()
        out = Output[int]()

        @contract(no_instant_loop=True, deterministic=True)
        def step(self, ctx: Context):
            ctx.write(self.outputs["out"], ctx.read(self.inputs["inp"]))

    runtime = GraphRuntime()
    producer = DelayProducer("delay")
    consumer = EarlyConsumer("consumer")
    runtime.add_node(producer)
    runtime.add_node(consumer)
    runtime.connect(producer.outputs["out"], consumer.inputs["inp"])

    runtime.build_schedule()
    flat_schedule = [nid for scc in runtime.schedule for nid in scc]
    assert flat_schedule.index(consumer.id) < flat_schedule.index(producer.id)

    runtime.run_tick(inputs={producer.inputs["x"]: 5})
    assert runtime.port_state[consumer.outputs["out"]] == -1

    runtime.run_tick(inputs={producer.inputs["x"]: 7})
    assert runtime.port_state[consumer.outputs["out"]] == 5


class OrderedReactionsNode(CoreNode):
    out = Output[int]()

    @reaction
    def first(self) -> Expr[int]:
        return 1

    @reaction
    def second(self) -> Expr[int]:
        return 2


def test_reactions_preserve_declaration_order():
    node = OrderedReactionsNode("Order")
    assert [r.name for r in node.reactions] == ["first", "second"]
