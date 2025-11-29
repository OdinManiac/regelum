from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Literal, Any
from rg_compiler.core.types import NodeId
from rg_compiler.core.dsl import Expr
from rg_compiler.core.contracts import Contract

@dataclass
class IRReaction:
    id: str
    reads_vars: Set[str] = field(default_factory=set)
    writes_vars: Set[str] = field(default_factory=set)
    ast: Optional[Expr[Any]] = None
    explicit_writes: Dict[str, Expr[Any]] = field(default_factory=dict)
    output_port: Optional[str] = None # Added field
    has_delay_output: bool = False
    contract: Optional[Contract] = None 
    is_unsafe: bool = False
    unsafe_reason: Optional[str] = None
    python_method: Optional[Any] = None
    nonzeno_rank: Optional[str] = None
    nonzeno_limit: Optional[int] = None

@dataclass
class IRPort:
    name: str
    rate: Optional[int] = None
    has_default: bool = False
    default_value: Any = None

@dataclass
class IRNode:
    id: NodeId
    kind: Literal["Raw", "Core", "Ext", "Continuous"]
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    input_meta: Dict[str, IRPort] = field(default_factory=dict)
    output_meta: Dict[str, IRPort] = field(default_factory=dict)
    reactions: List[IRReaction] = field(default_factory=list)
    continuous_state_names: List[str] = field(default_factory=list)

@dataclass
class IREdge:
    src_node: NodeId
    src_port: str
    dst_node: NodeId
    dst_port: str

@dataclass
class IRVariable:
    name: str
    policy: str
    has_init: bool = True
    allows_multiwriter: bool = False
    is_monotone: bool = False
    height_bound: Optional[int] = None
    is_delay_buffer: bool = False
    init_value: Any = None

@dataclass
class IRGraph:
    nodes: Dict[NodeId, IRNode] = field(default_factory=dict)
    edges: List[IREdge] = field(default_factory=list)
    variables: Dict[str, IRVariable] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
