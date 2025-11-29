from typing import List, Any
from regelum.ir.graph import IRGraph
from regelum.compiler.pipeline import Pass, DiagnosticSink, DiagnosticSeverity

class CompilationReport:
    def __init__(self, ir: IRGraph, diagnostics: List[Any]):
        self.ir = ir
        self.diagnostics = diagnostics
        
    def __str__(self):
        lines = []
        lines.append("RG Compiler Report")
        lines.append("==================")
        lines.append(f"Nodes: {len(self.ir.nodes)}")
        
        errors = [d for d in self.diagnostics if d.severity == DiagnosticSeverity.ERROR]
        warnings = [d for d in self.diagnostics if d.severity == DiagnosticSeverity.WARNING]
        
        lines.append(f"Errors: {len(errors)}")
        lines.append(f"Warnings: {len(warnings)}")
        
        if errors:
            lines.append("\nErrors:")
            for e in errors:
                lines.append(f"  [{e.code}] {e.message} @ {e.location}")
                
        if warnings:
            lines.append("\nWarnings:")
            for w in warnings:
                lines.append(f"  [{w.code}] {w.message} @ {w.location}")
                
        return "\n".join(lines)
