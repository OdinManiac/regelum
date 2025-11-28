import pytest
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.core.runtime import GraphRuntime

class Producer(CoreNode):
    out = Output[int]()
    @reaction
    def produce(self) -> Expr[int]: return 1

class Consumer(CoreNode):
    inp = Input[int]()
    @reaction
    def consume(self, inp: Expr[int]): pass

def test_wiring_dsl():
    runtime = GraphRuntime()
    p = Producer("P")
    c = Consumer("C")
    
    # Must add nodes first to bind runtime
    runtime.add_node(p)
    runtime.add_node(c)
    
    # Test accessors
    assert p.o.out.name == "out"
    assert c.i.inp.name == "inp"
    
    # Test wiring
    p.o.out >> c.i.inp
    
    # Verify connection in runtime
    # edges: dst -> src
    # Note: CoreNode names ports based on attribute names.
    # p.outputs["out"] and c.inputs["inp"]
    
    # Access raw ports
    src_port = p.outputs["out"]
    dst_port = c.inputs["inp"]
    
    assert dst_port in runtime.edges
    assert src_port in runtime.edges[dst_port]

def test_wiring_before_add_raises():
    runtime = GraphRuntime()
    p = Producer("P")
    c = Consumer("C")
    
    # Not added to runtime yet
    with pytest.raises(RuntimeError, match="must be added to a GraphRuntime"):
        p.o.out >> c.i.inp
