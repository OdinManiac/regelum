import pytest
from rg_compiler.core.node import RawNode, Context
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.values import ABSENT, is_absent, is_present

class Emitter(RawNode):
    def __init__(self, id, val):
        super().__init__(id)
        self.val = val
        self.out = self.add_output("out")
        
    def step(self, ctx: Context) -> None:
        if self.val is not None:
            ctx.write(self.out, self.val)

class Receiver(RawNode):
    def __init__(self, id):
        super().__init__(id)
        self.inp = self.add_input("in")
        self.last_read = None
        
    def step(self, ctx: Context) -> None:
        self.last_read = ctx.read(self.inp)

def test_absent_value():
    runtime = GraphRuntime()
    e = Emitter("E", None) # Emits nothing (Absent)
    r = Receiver("R")
    runtime.add_node(e)
    runtime.add_node(r)
    runtime.connect(e.out, r.inp)
    
    runtime.build_schedule()
    runtime.run_tick() # Should clear ports
    
    assert r.last_read is ABSENT
    assert is_absent(r.last_read)
    assert not is_present(r.last_read)

def test_present_value():
    runtime = GraphRuntime()
    e = Emitter("E", 123)
    r = Receiver("R")
    runtime.add_node(e)
    runtime.add_node(r)
    runtime.connect(e.out, r.inp)
    
    runtime.build_schedule()
    runtime.run_tick()
    
    assert r.last_read == 123
    assert is_present(r.last_read)
    assert not is_absent(r.last_read)

def test_reset_behavior():
    # Tick 1: Present
    # Tick 2: Absent
    runtime = GraphRuntime()
    e = Emitter("E", 10)
    r = Receiver("R")
    runtime.add_node(e)
    runtime.add_node(r)
    runtime.connect(e.out, r.inp)
    runtime.build_schedule()
    
    runtime.run_tick()
    assert r.last_read == 10
    
    # Change emitter to emit None (Absent)
    e.val = None
    runtime.run_tick()
    assert r.last_read is ABSENT

