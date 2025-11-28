from typing import List, Any, Optional
from collections import defaultdict
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.node import RawNode
from rg_compiler.compiler.pipeline import CompilerPipeline, CompilerConfig
from rg_compiler.compiler.passes import StructuralPass, TypeCheckPass, CausalityPass, WriteConflictPass, InitPass
from rg_compiler.compiler.passes_sdf import SDFPass
from rg_compiler.compiler.report import CompilationReport

class Pipeline:
    def __init__(self, mode: str = "pragmatic"):
        self.runtime = GraphRuntime()
        self.mode = mode
        self._compiled = False
    
    def add(self, *nodes: RawNode):
        for node in nodes:
            self.runtime.add_node(node)
    
    def auto_wire(self, strict: bool = True):
        """
        Automatically connect ports with matching names.
        
        Strategy:
        1. Index all outputs by name.
        2. Iterate all unconnected inputs.
        3. If input name matches an output name:
           - If exactly one output matches -> Connect.
           - If multiple outputs match -> 
             - If strict=True -> Error (Ambiguity).
             - If strict=False -> Skip/Warn.
           - If no outputs match -> Skip (StructuralPass will catch unconnected later).
        """
        outputs_by_name = defaultdict(list)
        nodes = list(self.runtime.nodes.values())
        
        # Index outputs
        for node in nodes:
            for name, port in node.outputs.items():
                outputs_by_name[name].append((node, port))
        
        # Connect inputs
        connections_made = 0
        for node in nodes:
            for name, port in node.inputs.items():
                if port in self.runtime.edges:
                    continue # Already connected
                
                if name in outputs_by_name:
                    sources = outputs_by_name[name]
                    if len(sources) == 1:
                        src_node, src_port = sources[0]
                        # Prevent self-loop auto-wiring if unintended? 
                        # Usually valid in feedback, but maybe warn?
                        # Let's allow it, CausalityPass will check it.
                        self.runtime.connect(src_port, port)
                        connections_made += 1
                        print(f"Auto-wired: {src_node.id}.{src_port.name} -> {node.id}.{port.name}")
                    elif len(sources) > 1:
                        msg = f"Ambiguous auto-wire for port '{name}': found sources {[n.id for n, p in sources]}"
                        if strict:
                            raise ValueError(msg)
                        else:
                            print(f"WARNING: {msg}. Skipping.")
        
        print(f"Auto-wiring completed. {connections_made} connections established.")

    def compile(self) -> bool:
        compiler = CompilerPipeline(CompilerConfig(mode=self.mode))
        compiler.add_pass(StructuralPass())
        compiler.add_pass(TypeCheckPass())
        compiler.add_pass(CausalityPass())
        compiler.add_pass(WriteConflictPass())
        compiler.add_pass(InitPass())
        compiler.add_pass(SDFPass())
        
        ir = compiler.build_ir(self.runtime)
        res = compiler.run_passes(ir)
        
        self.report = CompilationReport(ir, res.diagnostics)
        if not res.success:
            print(self.report)
            return False
            
        self.runtime.build_schedule()
        self._compiled = True
        return True
        
    def run(self, ticks: int = 1, inputs: dict = None):
        if not self._compiled:
            if not self.compile():
                raise RuntimeError("Pipeline compilation failed")
                
        print(f"Running pipeline for {ticks} ticks...")
        for _ in range(ticks):
            self.runtime.run_tick(inputs=inputs)
