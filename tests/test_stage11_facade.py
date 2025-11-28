import pytest
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Expr
from rg_compiler.api import Pipeline

class Inc(CoreNode):
    # StructuralPass fixed to respect default
    inp = Input[int](default=0)
    out = Output[int]()
    
    @reaction
    def step(self, inp: Expr[int]) -> Expr[int]:
        # Wait, why was it failing with TypeError: RuntimeIntentContext + int?
        # Ah! I named my reaction 'step'.
        # RawNode has abstract method 'step(self, ctx)'.
        # CoreNode implements 'step(self, ctx)'.
        # If I define 'step' with custom signature in subclass, I OVERRIDE CoreNode.step!
        # And runtime calls node.step(ctx).
        # So python calls Inc.step(self, inp=ctx).
        # So inp is Context.
        # ctx + 1 -> TypeError.
        
        # FIX: Rename reaction method. Never name it 'step' in CoreNode subclass.
        return inp + 1

    @reaction
    def process(self, inp: Expr[int]) -> Expr[int]:
        return inp + 1

# Update test to use correct class definition
class IncSafe(CoreNode):
    inp = Input[int](default=0)
    out = Output[int]()
    
    @reaction
    def process(self, inp: Expr[int]) -> Expr[int]:
        return inp + 1

def test_pipeline_facade():
    pipe = Pipeline(mode="pragmatic")
    n1 = IncSafe("N1")
    n2 = IncSafe("N2")
    
    pipe.add(n1, n2)
    
    n1.o.out >> n2.i.inp
    
    pipe.run(ticks=1)
    
    assert pipe.runtime.port_state[n2.outputs["out"]] == 2
