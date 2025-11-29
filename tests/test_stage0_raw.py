import pytest
from typing import Any
from regelum.core.node import RawNode, Context
from regelum.core.runtime import GraphRuntime

class AdderNode(RawNode):
    def __init__(self, node_id: str, val: int = 1):
        super().__init__(node_id)
        self.val = val
        self.input = self.add_input("in")
        self.output = self.add_output("out")

    def step(self, ctx: Context) -> None:
        in_val = ctx.read(self.input)
        # If input is None (not connected or first step with no data), assume 0
        current = in_val if in_val is not None else 0
        result = current + self.val
        ctx.write(self.output, result)

class SourceNode(RawNode):
    def __init__(self, node_id: str, start_val: int = 0):
        super().__init__(node_id)
        self.val = start_val
        self.output = self.add_output("out")

    def step(self, ctx: Context) -> None:
        ctx.write(self.output, self.val)
        self.val += 1

class SinkNode(RawNode):
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.input = self.add_input("in")
        self.last_received: Any = None

    def step(self, ctx: Context) -> None:
        self.last_received = ctx.read(self.input)

def test_linear_graph():
    # A (Source) -> B (Adder) -> C (Sink)
    runtime = GraphRuntime()
    
    node_a = SourceNode("A", start_val=10)
    node_b = AdderNode("B", val=5)
    node_c = SinkNode("C")

    runtime.add_node(node_a)
    runtime.add_node(node_b)
    runtime.add_node(node_c)

    # Connect A.out -> B.in
    runtime.connect(node_a.output, node_b.input)
    # Connect B.out -> C.in
    runtime.connect(node_b.output, node_c.input)

    runtime.build_schedule()
    
    # Step 1
    # A writes 10
    # B reads ? (None, if execution order matters, but wait. 
    # In one tick:
    # A runs -> writes 10 to state.
    # B runs -> reads A.out from state. If A ran before B, it gets 10.
    # C runs -> reads B.out from state.
    # Topological sort ensures A -> B -> C.
    
    runtime.run_step()
    
    # Check results
    # A wrote 10.
    # B read 10, added 5, wrote 15.
    # C read 15.
    
    assert node_c.last_received == 15

    # Step 2
    runtime.run_step()
    # A writes 11 (val increments)
    # B reads 11 -> writes 16
    # C reads 16
    assert node_c.last_received == 16

def test_dag_merge():
    # A (Source) -> B (Adder) -\
    #                          -> D (Summer) -> E (Sink)
    # A (Source) -> C (Adder) -/
    
    class SummerNode(RawNode):
        def __init__(self, node_id: str):
            super().__init__(node_id)
            self.in1 = self.add_input("in1")
            self.in2 = self.add_input("in2")
            self.out = self.add_output("out")
            
        def step(self, ctx: Context) -> None:
            v1 = ctx.read(self.in1) or 0
            v2 = ctx.read(self.in2) or 0
            ctx.write(self.out, v1 + v2)

    runtime = GraphRuntime()
    
    a = SourceNode("A", start_val=10)
    b = AdderNode("B", val=1)
    c = AdderNode("C", val=2)
    d = SummerNode("D")
    e = SinkNode("E")
    
    for n in [a, b, c, d, e]:
        runtime.add_node(n)
        
    # A -> B
    runtime.connect(a.output, b.input)
    # A -> C
    runtime.connect(a.output, c.input)
    # B -> D.in1
    runtime.connect(b.output, d.in1)
    # C -> D.in2
    runtime.connect(c.output, d.in2)
    # D -> E
    runtime.connect(d.out, e.input)
    
    runtime.build_schedule()
    runtime.run_step()
    
    # A writes 10
    # B reads 10 -> writes 11
    # C reads 10 -> writes 12
    # D reads 11, 12 -> writes 23
    # E reads 23
    
    assert e.last_received == 23

def test_cycle_detection():
    # A -> B -> A
    runtime = GraphRuntime()
    
    a = AdderNode("A")
    b = AdderNode("B")
    
    runtime.add_node(a)
    runtime.add_node(b)
    
    runtime.connect(a.output, b.input)
    runtime.connect(b.output, a.input)
    
    # Cycles are now supported via SCCs
    runtime.build_schedule()
    
    # Schedule should be a single SCC containing A and B
    assert len(runtime.schedule) == 1
    scc = runtime.schedule[0]
    assert len(scc) == 2
    assert "A" in scc
    assert "B" in scc

