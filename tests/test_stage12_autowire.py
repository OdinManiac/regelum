import pytest
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.api import Pipeline

class Producer(CoreNode):
    data = Output[int]()
    @reaction
    def produce(self) -> Expr[int]: return 1

class Consumer(CoreNode):
    data = Input[int]()
    @reaction
    def consume(self, data: Expr[int]): pass

class AnotherProducer(CoreNode):
    data = Output[int]()
    @reaction
    def produce(self) -> Expr[int]: return 2

def test_autowire_success():
    pipe = Pipeline()
    p = Producer("P")
    c = Consumer("C")
    pipe.add(p, c)
    
    pipe.auto_wire(strict=True)
    
    # Check connection
    # C.data should be connected to P.data
    assert c.inputs["data"] in pipe.runtime.edges
    assert p.outputs["data"] in pipe.runtime.edges[c.inputs["data"]]

def test_autowire_ambiguity_strict():
    pipe = Pipeline()
    p1 = Producer("P1")
    p2 = AnotherProducer("P2") # Also has output 'data'
    c = Consumer("C") # Has input 'data'
    
    pipe.add(p1, p2, c)
    
    with pytest.raises(ValueError, match="Ambiguous auto-wire"):
        pipe.auto_wire(strict=True)

def test_autowire_partial():
    # Mix manual and auto
    pipe = Pipeline()
    p1 = Producer("P1")
    p2 = AnotherProducer("P2")
    c1 = Consumer("C1")
    c2 = Consumer("C2")
    
    pipe.add(p1, p2, c1, c2)
    
    # Manually wire P1 -> C1 (resolving ambiguity for C1)
    p1.o.data >> c1.i.data
    
    # Auto-wire remaining?
    # C2 needs 'data'. Both P1 and P2 provide 'data'.
    # Still ambiguous for C2!
    
    with pytest.raises(ValueError, match="Ambiguous"):
        pipe.auto_wire(strict=True)
        
    # If we manually wire ALL ambiguous ones
    # P2 -> C2
    p2.o.data >> c2.i.data
    
    # Now auto-wire should be happy (nothing to do or just verify?)
    pipe.auto_wire(strict=True)
    
    assert c1.inputs["data"] in pipe.runtime.edges
    assert c2.inputs["data"] in pipe.runtime.edges

