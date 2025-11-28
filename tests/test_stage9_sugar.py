import pytest
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.core_node import Input, Output
from rg_compiler.core.node import Context

class DeclarativeExtNode(ExtNode):
    inp = Input[int]()
    out = Output[int]()
    
    def step(self, ctx: Context):
        pass

def test_ext_node_declarative_ports():
    node = DeclarativeExtNode("ext1")
    
    # Check ports are created
    assert "inp" in node.inputs
    assert "out" in node.outputs
    
    # Check port names
    assert node.inputs["inp"].name == "inp"
    assert node.outputs["out"].name == "out"

