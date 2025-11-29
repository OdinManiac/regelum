from collections import defaultdict
from typing import Dict, Any, List
from regelum.ir.graph import IRGraph
from regelum.compiler.pipeline import Pass, DiagnosticSink

class SDFPass(Pass):
    name = "SDFPass"
    
    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        # 1. Detect SDF nodes
        sdf_nodes = set()
        for nid, node in ir.nodes.items():
            has_rate = False
            for p in node.input_meta.values():
                if p.rate is not None: has_rate = True
            for p in node.output_meta.values():
                if p.rate is not None: has_rate = True
            
            if has_rate:
                sdf_nodes.add(nid)
                
        if not sdf_nodes:
            return

        # 2. Build Topology Matrix Γ (Channels x Actors)
        channels = []
        
        for edge in ir.edges:
            if edge.src_node in sdf_nodes and edge.dst_node in sdf_nodes:
                channels.append(edge)
                
        if not channels:
            return
            
        # 3. Find Connected Components
        adj = defaultdict(list)
        for edge in channels:
            adj[edge.src_node].append(edge)
            adj[edge.dst_node].append(edge) 
            
        seen = set()
        components = []
        for nid in sdf_nodes:
            if nid not in seen:
                comp = []
                stack = [nid]
                seen.add(nid)
                while stack:
                    u = stack.pop()
                    comp.append(u)
                    for e in adj[u]:
                        v = e.dst_node if e.src_node == u else e.src_node
                        if v not in seen:
                            seen.add(v)
                            stack.append(v)
                components.append(comp)

        # 4. Solve Balance Equations for each component
        for comp in components:
            q = {}
            start_node = comp[0]
            q[start_node] = 1.0
            
            stack = [start_node]
            valid = True
            
            while stack:
                u = stack.pop()
                
                # Outgoing edges from u
                for edge in channels:
                    if edge.src_node == u:
                        v = edge.dst_node
                        if v not in comp: continue 
                        
                        src_node = ir.nodes[u]
                        dst_node = ir.nodes[v]
                        prod = src_node.output_meta[edge.src_port].rate
                        cons = dst_node.input_meta[edge.dst_port].rate
                        
                        if prod is None: prod = 1
                        if cons is None: cons = 1
                        
                        expected_q_v = q[u] * (prod / cons)
                        
                        if v in q:
                            if abs(q[v] - expected_q_v) > 1e-9:
                                diag.error("SDF001", f"Inconsistent rates between {u} and {v}. "
                                                     f"Path requires firing ratio {expected_q_v/q[u]}, but another path differs.", 
                                                     location=str(u))
                                valid = False
                                break
                        else:
                            q[v] = expected_q_v
                            stack.append(v)
                            
                    elif edge.dst_node == u:
                        v = edge.src_node
                        if v not in comp: continue

                        src_node = ir.nodes[v]
                        dst_node = ir.nodes[u]
                        prod = src_node.output_meta[edge.src_port].rate
                        cons = dst_node.input_meta[edge.dst_port].rate
                        
                        if prod is None: prod = 1
                        if cons is None: cons = 1
                        
                        expected_q_v = q[u] * (cons / prod)
                        
                        if v in q:
                            if abs(q[v] - expected_q_v) > 1e-9:
                                diag.error("SDF001", f"Inconsistent rates between {v} and {u}.", location=str(u))
                                valid = False
                                break
                        else:
                            q[v] = expected_q_v
                            stack.append(v)
                
                if not valid: break
            
            if valid:
                # 5. Check against "implicit single clock" assumption.
                min_q = min(q.values())
                normalized_q = {k: v/min_q for k,v in q.items()}
                
                all_uniform = all(abs(v - 1.0) < 1e-9 for v in normalized_q.values())
                
                if not all_uniform:
                    q_str = ", ".join([f"{k}:{v:.2f}" for k,v in normalized_q.items()])
                    diag.warning("SDF001", f"Potential unbounded buffer or starvation. "
                                         f"Rates imply multi-rate schedule ({q_str}), but single-clock execution is assumed.",
                                         location="SDF")
