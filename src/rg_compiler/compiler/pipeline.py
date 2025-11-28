from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Any, get_type_hints, Set
from enum import Enum
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.node import RawNode
from rg_compiler.core.core_node import CoreNode
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.dsl import Expr, Var, If, BinOp, Cmp, Delay
from rg_compiler.ir.graph import IRGraph, IRNode, IRReaction, IREdge, IRVariable, IRPort

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

def collect_vars(expr: Expr[Any]) -> Set[str]:
    vars_ = set()
    if isinstance(expr, Var):
        vars_.add(expr.name)
    elif isinstance(expr, If):
        vars_.update(collect_vars(expr.cond))
        vars_.update(collect_vars(expr.then_))
        vars_.update(collect_vars(expr.else_))
    elif isinstance(expr, BinOp):
        vars_.update(collect_vars(expr.left))
        vars_.update(collect_vars(expr.right))
    elif isinstance(expr, Cmp):
        vars_.update(collect_vars(expr.left))
        vars_.update(collect_vars(expr.right))
    elif isinstance(expr, Delay):
        # Delayed variables are NOT instant dependencies.
        pass
    return vars_

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
                    policy_name = type(var.write_policy).__name__
                    has_init = var.init is not None
                    irvar = IRVariable(name=var.name, policy=policy_name, has_init=has_init)
                    ir.variables[var.name] = irvar

                hints = get_type_hints(type(node))
                for name in node.inputs:
                    inputs[name] = str(hints.get(name, "Any"))
                    port = node.inputs[name]
                    rate = getattr(port, "rate", None)
                    # Propagate 'default' existence
                    default = getattr(port, "default", None)
                    has_default = default is not None
                    input_meta[name] = IRPort(name, rate, has_default)
                
                for name in node.outputs:
                    outputs[name] = str(hints.get(name, "Any"))
                    port = node.outputs[name]
                    rate = getattr(port, "rate", None)
                    output_meta[name] = IRPort(name, rate)
                        
                for cr in node.reactions:
                    ast_vars = collect_vars(cr.ast)
                    reads = set()
                    for v in ast_vars:
                        if v in node._state_vars:
                            reads.add(node._state_vars[v].name)
                        elif v in node.inputs:
                            reads.add(v)
                    
                    writes = set()
                    explicit_writes = {}
                    for local_name, expr in cr.writes.items():
                        if local_name in node._state_vars:
                            global_name = node._state_vars[local_name].name
                            writes.add(global_name)
                            explicit_writes[global_name] = expr
                            
                            write_expr_vars = collect_vars(expr)
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
                        output_port=cr.output_name # Pass output port
                    )
                    reactions.append(irr)
            else:
                if isinstance(node, ExtNode):
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
                    input_meta[name] = IRPort(name, rate, has_default)
                    
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
            
            ir_node = IRNode(
                id=node_id,
                kind=kind, # type: ignore
                inputs=inputs,
                outputs=outputs,
                reactions=reactions,
                input_meta=input_meta,
                output_meta=output_meta
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
            try:
                p.run(ir, diag)
            except Exception as e:
                diag.error("INTERNAL", f"Pass {p.name} crashed: {str(e)}")
                return CompileResult(success=False, diagnostics=diag.diagnostics, ir=ir)
                
        has_errors = any(d.severity == DiagnosticSeverity.ERROR for d in diag.diagnostics)
        
        return CompileResult(success=not has_errors, diagnostics=diag.diagnostics, ir=ir)
