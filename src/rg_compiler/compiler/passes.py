from collections import defaultdict
from typing import List, Dict, Set, Any, Optional
from rg_compiler.ir.graph import IRGraph, IRReaction
from rg_compiler.compiler.pipeline import Pass, DiagnosticSink
from rg_compiler.core.ternary import V3, B3
from rg_compiler.core.interpreter import eval_expr_3val
from rg_compiler.core.dsl import Expr, Var, If, BinOp, Cmp, Delay

class StructuralPass(Pass):
    name = "StructuralPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        connected_dsts = set()
        for edge in ir.edges:
            connected_dsts.add((edge.dst_node, edge.dst_port))
            
        for node_id, node in ir.nodes.items():
            for input_name in node.inputs:
                is_connected = (node_id, input_name) in connected_dsts
                if not is_connected:
                    has_default = False
                    if input_name in node.input_meta:
                        if node.input_meta[input_name].has_default:
                            has_default = True
                    
                    if not has_default:
                        diag.error("STRUCT001", f"Input port '{input_name}' of node '{node_id}' is unconnected", location=str(node_id))

class TypeCheckPass(Pass):
    name = "TypeCheckPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        for edge in ir.edges:
            src_node = ir.nodes.get(edge.src_node)
            dst_node = ir.nodes.get(edge.dst_node)
            
            if not src_node or not dst_node:
                continue
                
            src_type = src_node.outputs.get(edge.src_port, "Any")
            dst_type = dst_node.inputs.get(edge.dst_port, "Any")
            
            if src_type == "Any" or dst_type == "Any":
                continue
                
            if src_type != dst_type:
                diag.warning("TYPE001", f"Type mismatch: {src_node.id}.{edge.src_port} ({src_type}) -> {dst_node.id}.{edge.dst_port} ({dst_type})", location=str(edge.src_node))

class WriteConflictPass(Pass):
    name = "WriteConflictPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        strict = ir.config.get("mode") == "strict"
        writers = defaultdict(list)
        
        for node_id, node in ir.nodes.items():
            for r in node.reactions:
                for var_name in r.writes_vars:
                    writers[var_name].append((node_id, r.id))
                    
        for var_name, writer_list in writers.items():
            if len(writer_list) > 1:
                policy = "LWWPolicy"
                if var_name in ir.variables:
                    policy = ir.variables[var_name].policy
                
                if policy == "ErrorPolicy":
                    diag.error("WRITE001", f"Multiple writers for variable '{var_name}' with ErrorPolicy: {writer_list}", location=str(var_name))
                elif policy == "LWWPolicy":
                    msg = f"Multiple writers for variable '{var_name}' with LWWPolicy. Determinism depends on schedule."
                    if strict:
                        diag.error("WRITE002", msg + " Strict mode requires explicit policy or single writer.", location=str(var_name))
                    else:
                        diag.warning("WRITE002", msg, location=str(var_name))

class CausalityPass(Pass):
    name = "CausalityPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        adj = defaultdict(list)
        nodes = set()
        
        reaction_map = {} 
        
        # Pre-scan edges to populate implicit ports for Raw/Ext nodes
        port_direction = {} 
        for edge in ir.edges:
            port_direction[(edge.src_node, edge.src_port)] = "out"
            port_direction[(edge.dst_node, edge.dst_port)] = "in"

        # 1. Build internal node graphs
        for node_id, node in ir.nodes.items():
            # Treat Raw nodes same as Ext nodes (Implicit dependencies) if they lack explicit structure
            if node.kind == "Ext" or node.kind == "Raw":
                if not node.reactions: continue
                r = node.reactions[0] # "step"
                rid = f"R:{node_id}:{r.id}"
                nodes.add(rid)
                reaction_map[rid] = r
                
                no_loop = False
                if r.contract and r.contract.no_instant_loop:
                    no_loop = True
                
                known_inputs = set(node.inputs.keys())
                known_outputs = set(node.outputs.keys())
                
                for (nid, pname), dir_ in port_direction.items():
                    if nid == node_id:
                        if dir_ == "in": known_inputs.add(pname)
                        elif dir_ == "out": known_outputs.add(pname)
                
                for inp in known_inputs:
                    pid = f"P:{node_id}.{inp}"
                    nodes.add(pid)
                    if not no_loop:
                        adj[pid].append(rid)
                
                for outp in known_outputs:
                    pid = f"P:{node_id}.{outp}"
                    nodes.add(pid)
                    adj[rid].append(pid)
            else:
                # CoreNode: Explicit dependencies
                for r in node.reactions:
                    rid = f"R:{node_id}:{r.id}"
                    nodes.add(rid)
                    reaction_map[rid] = r
                    
                    no_loop = False
                    if r.contract and r.contract.no_instant_loop:
                        no_loop = True
                    
                    for v in r.reads_vars:
                        if v in ir.variables: 
                            vid = f"V:{v}"
                        else:
                            vid = f"P:{node_id}.{v}"
                        nodes.add(vid)
                        if not no_loop:
                            adj[vid].append(rid)
                        
                    for v in r.writes_vars:
                        vid = f"V:{v}"
                        nodes.add(vid)
                        adj[rid].append(vid)
                    
                    if r.output_port:
                        oid = f"P:{node_id}.{r.output_port}"
                        nodes.add(oid)
                        adj[rid].append(oid)
                            
        # 2. Add Edges (Output -> Input)
        for edge in ir.edges:
            src_p = f"P:{edge.src_node}.{edge.src_port}"
            dst_p = f"P:{edge.dst_node}.{edge.dst_port}"
            
            if src_p in nodes and dst_p in nodes:
                adj[src_p].append(dst_p)

        # 3. Find SCCs
        sccs = self._tarjan(list(nodes), adj)
        
        for scc in sccs:
            if len(scc) > 1:
                r_nodes = [n for n in scc if n.startswith("R:")]
                
                is_core = True
                for rn in r_nodes:
                    _, nid, _ = rn.split(":", 2)
                    if ir.nodes[nid].kind != "Core":
                        is_core = False
                        break
                
                if not is_core:
                    diag.error("CAUS001", f"Algebraic cycle involving non-Core nodes: {scc}", location="SCC")
                else:
                    if not self._check_constructive(scc, reaction_map, ir):
                        diag.error("CAUS003", f"Non-constructive cycle detected: {scc}", location="SCC")
                        
            elif len(scc) == 1:
                elem = scc[0]
                if elem in adj[elem]:
                    if elem.startswith("R:"):
                        _, nid, _ = elem.split(":", 2)
                        if ir.nodes[nid].kind == "Core":
                            if not self._check_constructive([elem], reaction_map, ir):
                                diag.error("CAUS003", f"Non-constructive self-loop: {elem}", location="SCC")
                        else:
                             diag.error("CAUS002", f"Self-loop detected: {elem}", location=elem)

    def _collect_vars(self, expr: Expr[Any]) -> Set[str]:
        from rg_compiler.core.dsl import Var, If, BinOp, Cmp, Delay
        vars_ = set()
        if isinstance(expr, Var):
            vars_.add(expr.name)
        elif isinstance(expr, If):
            vars_.update(self._collect_vars(expr.cond))
            vars_.update(self._collect_vars(expr.then_))
            vars_.update(self._collect_vars(expr.else_))
        elif isinstance(expr, BinOp):
            vars_.update(self._collect_vars(expr.left))
            vars_.update(self._collect_vars(expr.right))
        elif isinstance(expr, Cmp):
            vars_.update(self._collect_vars(expr.left))
            vars_.update(self._collect_vars(expr.right))
        elif isinstance(expr, Delay):
            pass
        return vars_

    def _tarjan(self, vertices: List[str], adj: Dict[str, List[str]]) -> List[List[str]]:
        index = {}
        lowlink = {}
        stack = []
        on_stack = set()
        sccs = []
        idx = 0
        
        def strongconnect(v):
            nonlocal idx
            index[v] = idx
            lowlink[v] = idx
            idx += 1
            stack.append(v)
            on_stack.add(v)
            
            for w in adj[v]:
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])
            
            if lowlink[v] == index[v]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == v: break
                sccs.append(scc)
                
        for v in vertices:
            if v not in index:
                strongconnect(v)
        return sccs

    def _check_constructive(self, scc: List[str], reaction_map: Dict[str, IRReaction], ir: IRGraph) -> bool:
        scc_vars = {n for n in scc if n.startswith("V:") or n.startswith("P:")}
        scc_reactions = [n for n in scc if n.startswith("R:")]
        
        env: Dict[str, V3[Any]] = {}
        
        for _ in range(10): 
            changed = False
            current_env = env.copy()
            
            for rid in scc_reactions:
                r = reaction_map[rid]
                _, nid, _ = rid.split(":", 2)
                
                for global_var, expr in r.explicit_writes.items():
                    local_env = {}
                    ast_vars = self._collect_vars(expr)
                    for v in ast_vars:
                        # Construct global name from local name 'v'
                        global_candidate = f"{nid}.{v}"
                        
                        if global_candidate in ir.variables:
                            scoped_name = f"V:{global_candidate}"
                        else:
                            # Fallback: treat as Port/Input
                            scoped_name = f"P:{global_candidate}"
                        
                        if scoped_name in scc_vars:
                            local_env[v] = current_env.get(scoped_name, V3.bottom())
                        else:
                            local_env[v] = V3.known(0) 
                    
                    val = eval_expr_3val(expr, local_env)
                    var_key = f"V:{global_var}"
                    old_val = env.get(var_key, V3.bottom())
                    
                    if val.known and not old_val.known:
                        env[var_key] = val
                        changed = True
                
                if r.output_port and r.ast:
                    local_env = {}
                    ast_vars = self._collect_vars(r.ast)
                    for v in ast_vars:
                        global_candidate = f"{nid}.{v}"
                        if global_candidate in ir.variables:
                            scoped_name = f"V:{global_candidate}"
                        else:
                            scoped_name = f"P:{global_candidate}"
                            
                        if scoped_name in scc_vars:
                            local_env[v] = current_env.get(scoped_name, V3.bottom())
                        else:
                            local_env[v] = V3.known(0)
                    
                    val = eval_expr_3val(r.ast, local_env)
                    out_key = f"P:{nid}.{r.output_port}"
                    old_val = env.get(out_key, V3.bottom())
                    
                    if val.known and not old_val.known:
                        env[out_key] = val
                        changed = True
                        
            for edge in ir.edges:
                src_p = f"P:{edge.src_node}.{edge.src_port}"
                dst_p = f"P:{edge.dst_node}.{edge.dst_port}"
                
                if src_p in scc_vars and dst_p in scc_vars:
                    val = env.get(src_p, V3.bottom())
                    old_val = env.get(dst_p, V3.bottom())
                    if val.known and not old_val.known:
                        env[dst_p] = val
                        changed = True
            
            if not changed: break
            
        for v in scc_vars:
            if not env.get(v, V3.bottom()).known:
                return False
                
        return True

class InitPass(Pass):
    name = "InitPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        strict = ir.config.get("mode") == "strict"
        if not strict:
            return

        for name, var in ir.variables.items():
            if not var.has_init:
                diag.error("INIT001", f"Variable '{name}' has no initial value", location=name)

class NonZenoPass(Pass):
    name = "NonZenoPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        # Zeno behavior is infinite events in finite time.
        # In Synchronous model, this equates to infinite microsteps (divergence).
        # CausalityPass already checks for non-constructive loops (which don't converge).
        # This pass is a placeholder or checks for specific 'dangerous' patterns 
        # even if constructive (e.g. huge number of steps required).
        pass
