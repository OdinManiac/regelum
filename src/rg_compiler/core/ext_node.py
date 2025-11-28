from typing import Optional, TypeVar, Generic, get_type_hints
from .node import RawNode
from .core_node import Input, Output
from .contracts import Contract

T = TypeVar("T")

class ExtNode(RawNode):
    """
    Extended Node for user code (Python black box) with contracts.
    Supports declarative Input/Output definitions like CoreNode.
    """
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self._build_structure()

    def _build_structure(self):
        for name, attr in self.__class__.__dict__.items():
            if isinstance(attr, Input):
                # Pass default value to port creation
                port = self.add_input(name, default=attr.default)
                port.rate = attr.rate
            elif isinstance(attr, Output):
                port = self.add_output(name)
                port.rate = attr.rate
