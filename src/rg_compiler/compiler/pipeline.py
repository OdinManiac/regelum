from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Any, get_type_hints, Set
from enum import Enum
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.node import RawNode
from rg_compiler.core.core_node import CoreNode
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.hybrid_adapters import ContinuousWrapper
from rg_compiler.core.dsl import Expr, Var
from rg_compiler.ir.graph import IRGraph, IRNode, IRReaction, IREdge, IRVariable, IRPort
from rg_compiler.compiler.expr_utils import collect_expr_vars

class DiagnosticSeverity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"

@dataclass
class Diagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    location: Optional[str] = None

class DiagnosticSink:
    def __init__(self):
        self.diagnostics: List[Diagnostic] = []

    def error(self, code: str, message: str, location: Optional[str] = None):
        self.diagnostics.append(Diagnostic(DiagnosticSeverity.ERROR, code, message, location))

    def warning(self, code: str, message: str, location: Optional[str] = None):
        self.diagnostics.append(Diagnostic(DiagnosticSeverity.WARNING, code, message, location))

@dataclass
class CompilerConfig:
    mode: str = "best_effort" 

@dataclass
class CompileResult:
    success: bool
    diagnostics: List[Diagnostic]
    ir: Optional[IRGraph] = None

class Pass(ABC):
    name: str

    @abstractmethod
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        ...


class CompilerPipeline:
    def __init__(self, config: CompilerConfig):
        self.config = config
        self.passes: List[Pass] = []

    def add_pass(self, p: Pass):
        self.passes.append(p)

    def build_ir(self, runtime: GraphRuntime) -> IRGraph:
        ir = IRGraph(config={"mode": self.config.mode})
        
        for node_id, node in runtime.nodes.items():
            inputs = {}
            outputs = {}
            reactions = []
            kind = "Raw"
            input_meta = {}
            output_meta = {}
            
            if isinstance(node, CoreNode):
                kind = "Core"
                for var_name, var in node._state_vars.items():
                    policy = var.write_policy
                    policy_name = type(policy).__name__
                    has_init = var.init is not None
                    irvar = IRVariable(
                        name=var.name,
                        policy=policy_name,
                        has_init=has_init,
                        allows_multiwriter=policy.allows_multiwriter(),
                        is_monotone=policy.is_monotone(),
                        height_bound=policy.height_bound(),
                        is_delay_buffer=getattr(var, "is_delay_buffer", False),
                        init_value=var.init if has_init else None,
                    )
                    ir.variables[var.name] = irvar

                hints = get_type_hints(type(node))
                for name in node.inputs:
                    inputs[name] = str(hints.get(name, "Any"))
                    port = node.inputs[name]
                    rate = getattr(port, "rate", None)
                    default = getattr(port, "default", None)
                    has_default = default is not None
                    input_meta[name] = IRPort(name, rate, has_default, default)
                
                for name in node.outputs:
                    outputs[name] = str(hints.get(name, "Any"))
                    port = node.outputs[name]
                    rate = getattr(port, "rate", None)
                    output_meta[name] = IRPort(name, rate)
                        
                for cr in node.reactions:
                    ast_vars = collect_expr_vars(cr.ast)
                    reads = set()
                    for v in ast_vars:
                        if v in node._state_vars:
                            reads.add(node._state_vars[v].name)
                        elif v in node.inputs:
                            reads.add(v)
                    
                    writes = set()
                    explicit_writes = {}
                    has_delay_output = False
                    if cr.output_name and isinstance(cr.ast, Var):
                        local_name = cr.ast.name
                        if local_name in node._state_vars and getattr(node._state_vars[local_name], "is_delay_buffer", False):
                            has_delay_output = True
                    for local_name, expr in cr.writes.items():
                        if local_name in node._state_vars:
                            state_var = node._state_vars[local_name]
                            global_name = state_var.name
                            writes.add(global_name)
                            explicit_writes[global_name] = expr
                            
                            if not getattr(state_var, "is_delay_buffer", False):
                                write_expr_vars = collect_expr_vars(expr)
                                for wv in write_expr_vars:
                                    if wv in node._state_vars:
                                        reads.add(node._state_vars[wv].name)
                                    elif wv in node.inputs:
                                        reads.add(wv)

                    irr = IRReaction(
                        id=cr.name, 
                        reads_vars=reads, 
                        writes_vars=writes, 
                        ast=cr.ast,
                        explicit_writes=explicit_writes,
                        output_port=cr.output_name,
                        has_delay_output=has_delay_output,
                        nonzeno_rank=cr.nonzeno_rank,
                        nonzeno_limit=cr.nonzeno_limit,
                    )
                    reactions.append(irr)
            else:
                if isinstance(node, ContinuousWrapper):
                    kind = "Continuous"
                elif isinstance(node, ExtNode):
                    kind = "Ext"
                
                step_method = getattr(node, "step", None)
                contract = getattr(step_method, "_contract", None)
                is_unsafe = getattr(step_method, "_unsafe", False)
                unsafe_reason = getattr(step_method, "_unsafe_reason", None)
                
                for name, port in node.inputs.items():
                    inputs[name] = "Any"
                    rate = getattr(port, "rate", None)
                    default = getattr(port, "default", None)
                    has_default = default is not None
                    input_meta[name] = IRPort(name, rate, has_default, default)
                    
                for name, port in node.outputs.items():
                    outputs[name] = "Any"
                    rate = getattr(port, "rate", None)
                    output_meta[name] = IRPort(name, rate)
                
                reactions.append(IRReaction(
                    id="step",
                    contract=contract,
                    is_unsafe=is_unsafe,
                    unsafe_reason=unsafe_reason,
                    python_method=step_method
                ))
            continuous_state_names: List[str] = []
            if isinstance(node, ContinuousWrapper):
                continuous_state_names = node.inner._state_names()  # noqa: SLF001

            ir_node = IRNode(
                id=node_id,
                kind=kind, # type: ignore
                inputs=inputs,
                outputs=outputs,
                reactions=reactions,
                input_meta=input_meta,
                output_meta=output_meta,
                continuous_state_names=continuous_state_names,
            )
            ir.nodes[node_id] = ir_node
            
        for dst, sources in runtime.edges.items():
            for src in sources:
                if src.node_id and dst.node_id:
                    edge = IREdge(
                        src_node=src.node_id,
                        src_port=src.name,
                        dst_node=dst.node_id,
                        dst_port=dst.name
                    )
                    ir.edges.append(edge)
                
        return ir

    def run_passes(self, ir: IRGraph) -> CompileResult:
        diag = DiagnosticSink()
        
        for p in self.passes:
            p.run(ir, diag)
                
        has_errors = any(d.severity == DiagnosticSeverity.ERROR for d in diag.diagnostics)
        
        return CompileResult(success=not has_errors, diagnostics=diag.diagnostics, ir=ir)
