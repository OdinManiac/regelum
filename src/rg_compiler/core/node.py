from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING, Union
from .types import NodeId

if TYPE_CHECKING:
    from .variables import Variable
    from .runtime import GraphRuntime

class Port:
    def __init__(self, name: str, default: Any = None):
        self.name = name
        self.node_id: Optional[NodeId] = None
        self.rate: Optional[int] = None 
        self.default: Any = default
        self._runtime: Optional['GraphRuntime'] = None 
        self.is_delay_output: bool = False
        self.delay_state_name: Optional[str] = None

    def __repr__(self) -> str:
        return f"Port(name={self.name}, node={self.node_id}, default={self.default})"

    def __rshift__(self, other: 'Port'):
        if not isinstance(other, Port):
            raise TypeError(f"Right operand of >> must be a Port, got {type(other)}")
        
        if self._runtime:
            self._runtime.connect(self, other)
        elif other._runtime:
            other._runtime.connect(self, other)
        else:
            raise RuntimeError("Cannot use >> operator: Nodes must be added to a GraphRuntime first.")
        
        return other

class Context(ABC):
    @abstractmethod
    def read(self, port: Port) -> Any:
        """Read value from a port."""
        ...

    @abstractmethod
    def write(self, port: Port, value: Any) -> None:
        """Write value to a port."""
        ...

class IntentContext(Context):
    @abstractmethod
    def read_var(self, var: "Variable[Any]") -> Any:
        """Read current value of a variable."""
        ...

    @abstractmethod
    def write_var(self, var: "Variable[Any]", value: Any) -> None:
        """Propose a value for a variable."""
        ...

class PortAccessor:
    """Helper to access ports via dot notation: node.i.port_name"""
    def __init__(self, ports: Dict[str, Port]):
        self._ports = ports
        
    def __getattr__(self, name: str) -> Port:
        if name in self._ports:
            return self._ports[name]
        raise AttributeError(f"Port '{name}' not found")
        
    def __getitem__(self, name: str) -> Port:
        return self._ports[name]

class RawNode(ABC):
    def __init__(self, node_id: str):
        self.id = NodeId(node_id)
        self.inputs: Dict[str, Port] = {}
        self.outputs: Dict[str, Port] = {}
        self._runtime: Optional['GraphRuntime'] = None
        self._no_instant_loop: bool = False

    @property
    def i(self) -> PortAccessor:
        return PortAccessor(self.inputs)
        
    @property
    def o(self) -> PortAccessor:
        return PortAccessor(self.outputs)

    @abstractmethod
    def step(self, ctx: Context) -> None:
        """Execute one step of the node."""
        ...

    def add_input(self, name: str, default: Any = None) -> Port:
        port = Port(name, default=default)
        port.node_id = self.id
        self.inputs[name] = port
        return port

    def add_output(self, name: str) -> Port:
        port = Port(name)
        port.node_id = self.id
        self.outputs[name] = port
        return port
    
    def bind_runtime(self, runtime: 'GraphRuntime'):
        self._runtime = runtime
        for p in self.inputs.values():
            p._runtime = runtime
        for p in self.outputs.values():
            p._runtime = runtime
