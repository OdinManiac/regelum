from collections import defaultdict
from typing import List, Dict, Set, Any, Optional

from rg_compiler.ir.graph import IRGraph, IRReaction
from rg_compiler.compiler.pipeline import Pass, DiagnosticSink
from rg_compiler.core.ternary import V3, Presence
from rg_compiler.core.interpreter import eval_expr_3val
from rg_compiler.core.dsl import Expr, Var, If, BinOp, Cmp, Delay


class StructuralPass(Pass):
    name = "StructuralPass"

    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        connected_dsts = set()
        dst_counts = defaultdict(int)

        for edge in ir.edges:
            key = (edge.dst_node, edge.dst_port)
            connected_dsts.add(key)
            dst_counts[key] += 1

        for (node_id, port_name), count in dst_counts.items():
            if count > 1:
                diag.error(
                    "STRUCT002",
                    f"Port '{node_id}.{port_name}' has {count} incoming edges (Fan-in > 1). Use a Merge node.",
                    location=str(node_id),
                )

        for node_id, node in ir.nodes.items():
            for input_name in node.inputs:
                is_connected = (node_id, input_name) in connected_dsts
                if not is_connected:
                    has_default = False
                    if input_name in node.input_meta and node.input_meta[input_name].has_default:
                        has_default = True

                    if not has_default:
                        diag.error(
                            "STRUCT001",
                            f"Input port '{input_name}' of node '{node_id}' is unconnected",
                            location=str(node_id),
                        )


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
                diag.warning(
                    "TYPE001",
                    f"Type mismatch: {src_node.id}.{edge.src_port} ({src_type}) -> {dst_node.id}.{edge.dst_port} ({dst_type})",
                    location=str(edge.src_node),
                )


class ContinuousPass(Pass):
    name = "ContinuousPass"

    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        for node in ir.nodes.values():
            if node.kind != "Continuous":
                continue
            # Require dt input with default > 0 to avoid zero-time integration.
            dt_meta = node.input_meta.get("dt")
            if not dt_meta or not dt_meta.has_default:
                diag.error(
                    "CT001",
                    f"Continuous node '{node.id}' must have input 'dt' with a positive default",
                    location=str(node.id),
                )
                continue
            if dt_meta.default_value is None or dt_meta.default_value <= 0:
                diag.error(
                    "CT002",
                    f"Continuous node '{node.id}' has non-positive dt default: {dt_meta.default_value}",
                    location=str(node.id),
                )
            # Check standard port names
            if "state" not in node.outputs or "y" not in node.outputs:
                diag.warning(
                    "CT003",
                    f"Continuous node '{node.id}' should expose 'state' and 'y' outputs",
                    location=str(node.id),
                )


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
                    diag.error(
                        "WRITE001",
                        f"Multiple writers for variable '{var_name}' with ErrorPolicy: {writer_list}",
                        location=str(var_name),
                    )
                elif policy == "LWWPolicy":
                    msg = f"Multiple writers for variable '{var_name}' with LWWPolicy. Determinism depends on schedule."
                    if strict:
                        diag.error(
                            "WRITE002",
                            msg + " Strict mode requires explicit policy or single writer.",
                            location=str(var_name),
                        )
                    else:
                        diag.warning("WRITE002", msg, location=str(var_name))


class CausalityPass(Pass):
    name = "CausalityPass"

    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        adj = defaultdict(list)
        nodes = set()
        reaction_map: Dict[str, IRReaction] = {}

        port_direction = {}
        for edge in ir.edges:
            port_direction[(edge.src_node, edge.src_port)] = "out"
            port_direction[(edge.dst_node, edge.dst_port)] = "in"

        for node_id, node in ir.nodes.items():
            if node.kind in ("Ext", "Raw", "Continuous"):
                if not node.reactions:
                    continue
                r = node.reactions[0]
                rid = f"R:{node_id}:{r.id}"
                nodes.add(rid)
                reaction_map[rid] = r

                no_loop = bool(r.contract and r.contract.no_instant_loop)

                known_inputs = set(node.inputs.keys())
                known_outputs = set(node.outputs.keys())

                for (nid, pname), direction in port_direction.items():
                    if nid == node_id:
                        if direction == "in":
                            known_inputs.add(pname)
                        elif direction == "out":
                            known_outputs.add(pname)

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
                for r in node.reactions:
                    rid = f"R:{node_id}:{r.id}"
                    nodes.add(rid)
                    reaction_map[rid] = r

                    no_loop = bool(r.contract and r.contract.no_instant_loop)

                    for v in r.reads_vars:
                        is_delay = False
                        if v in ir.variables:
                            vid = f"V:{v}"
                            var_meta = ir.variables[v]
                            is_delay = bool(getattr(var_meta, "is_delay_buffer", False))
                        else:
                            vid = f"P:{node_id}.{v}"
                        nodes.add(vid)
                        if not no_loop and not is_delay:
                            adj[vid].append(rid)

                    for v in r.writes_vars:
                        vid = f"V:{v}"
                        nodes.add(vid)
                        adj[rid].append(vid)

                    if r.output_port:
                        if not r.has_delay_output:
                            oid = f"P:{node_id}.{r.output_port}"
                            nodes.add(oid)
                            adj[rid].append(oid)

        delay_ports = set()
        for node_id, node in ir.nodes.items():
            for r in node.reactions:
                if r.has_delay_output and r.output_port:
                    delay_ports.add((node_id, r.output_port))

        for edge in ir.edges:
            src_p = f"P:{edge.src_node}.{edge.src_port}"
            dst_p = f"P:{edge.dst_node}.{edge.dst_port}"

            if src_p in nodes and dst_p in nodes:
                if (edge.src_node, edge.src_port) not in delay_ports:
                    adj[src_p].append(dst_p)

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
                    bad_vars = self._non_monotone_vars(scc, ir)
                    if bad_vars:
                        diag.error(
                            "CAUS004",
                            f"Cycle requires non-monotone state(s): {bad_vars}. Use Delay or monotone policy.",
                            location="SCC",
                        )
                        continue
                    ranks_present = any(reaction_map[rn].nonzeno_rank for rn in r_nodes if rn in reaction_map)
                    if ranks_present:
                        continue
                    if not self._check_constructive(scc, reaction_map, ir):
                        diag.error("CAUS003", f"Non-constructive cycle detected: {scc}", location="SCC")

            elif len(scc) == 1:
                elem = scc[0]
                if elem in adj[elem]:
                    if elem.startswith("R:"):
                        _, nid, _ = elem.split(":", 2)
                        if ir.nodes[nid].kind == "Core":
                            bad_vars = self._non_monotone_vars([elem], ir)
                            if bad_vars:
                                diag.error(
                                    "CAUS004",
                                    f"Self-loop touches non-monotone state(s): {bad_vars}",
                                    location=elem,
                                )
                                continue
                            reaction = reaction_map.get(elem)
                            if reaction and reaction.nonzeno_rank:
                                continue
                            if not self._check_constructive([elem], reaction_map, ir):
                                diag.error("CAUS003", f"Non-constructive self-loop: {elem}", location="SCC")
                        else:
                            diag.error("CAUS002", f"Self-loop detected: {elem}", location=elem)

    def _collect_vars(self, expr: Expr[Any]) -> Set[str]:
        vars_: Set[str] = set()
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
                    if w == v:
                        break
                sccs.append(scc)

        for v in vertices:
            if v not in index:
                strongconnect(v)
        return sccs

    @staticmethod
    def _join_values(old: V3[Any], new: V3[Any]) -> tuple[V3[Any], bool, bool]:
        if new.presence == Presence.BOTTOM:
            return old, False, False

        if old.presence == Presence.BOTTOM:
            return new, True, False

        if old.presence == Presence.ABSENT:
            if new.presence == Presence.ABSENT:
                return old, False, False
            if new.presence == Presence.PRESENT:
                return new, True, False
            return old, False, False

        if old.presence == Presence.PRESENT:
            if new.presence == Presence.ABSENT:
                return old, False, False
            if new.presence == Presence.PRESENT:
                if old.value == new.value:
                    return old, False, False
                return new, True, False

        return old, False, False

    def _check_constructive(self, scc: List[str], reaction_map: Dict[str, IRReaction], ir: IRGraph) -> bool:
        scc_vars = {n for n in scc if n.startswith("V:") or n.startswith("P:")}
        scc_reactions = [n for n in scc if n.startswith("R:")]

        env: Dict[str, V3[Any]] = {}
        for name in scc_vars:
            env[name] = self._baseline_value(name, ir)

        height_budget = 0
        for name in scc_vars:
            if name.startswith("V:"):
                var_name = name.split(":", 1)[1]
                var_meta = ir.variables.get(var_name)
                if var_meta and var_meta.height_bound is not None:
                    height_budget += var_meta.height_bound

        ITER_LIMIT = (height_budget + 1) if height_budget > 0 else 20
        converged = False
        for _ in range(ITER_LIMIT):
            changed_any = False
            current_env = env.copy()

            for rid in scc_reactions:
                r = reaction_map[rid]
                _, nid, _ = rid.split(":", 2)

                for global_var, expr in r.explicit_writes.items():
                    local_env = {}
                    ast_vars = self._collect_vars(expr)
                    for v in ast_vars:
                        global_candidate = f"{nid}.{v}"

                        if global_candidate in ir.variables:
                            scoped_name = f"V:{global_candidate}"
                        else:
                            scoped_name = f"P:{global_candidate}"

                        if scoped_name in scc_vars:
                            local_env[v] = current_env.get(scoped_name, V3.bottom())
                        else:
                            local_env[v] = self._baseline_value(scoped_name, ir)

                    val = eval_expr_3val(expr, local_env)
                    var_key = f"V:{global_var}"
                    old_val = env.get(var_key, V3.bottom())

                    merged, changed, conflict = self._join_values(old_val, val)
                    if conflict:
                        return False
                    if changed:
                        env[var_key] = merged
                        changed_any = True

                if r.output_port and r.ast is not None:
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
                            local_env[v] = self._baseline_value(scoped_name, ir)

                    val = eval_expr_3val(r.ast, local_env)
                    out_key = f"P:{nid}.{r.output_port}"
                    old_val = env.get(out_key, V3.bottom())

                    merged, changed, conflict = self._join_values(old_val, val)
                    if conflict:
                        return False
                    if changed:
                        env[out_key] = merged
                        changed_any = True

            for edge in ir.edges:
                src_p = f"P:{edge.src_node}.{edge.src_port}"
                dst_p = f"P:{edge.dst_node}.{edge.dst_port}"

                if src_p in scc_vars and dst_p in scc_vars:
                    val = env.get(src_p, V3.bottom())
                    old_val = env.get(dst_p, V3.bottom())
                    merged, changed, conflict = self._join_values(old_val, val)
                    if conflict:
                        return False
                    if changed:
                        env[dst_p] = merged
                        changed_any = True

            if not changed_any:
                converged = True
                break

        if not converged:
            return False
        for v in scc_vars:
            if env.get(v, V3.bottom()).presence == Presence.BOTTOM:
                return False

        return True

    def _baseline_value(self, scoped_name: str, ir: IRGraph) -> V3[Any]:
        if scoped_name.startswith("V:"):
            name = scoped_name.split(":", 1)[1]
            var = ir.variables.get(name)
            if var is None:
                return V3.bottom()
            if var.has_init:
                return V3.present(var.init_value)
            return V3.bottom()

        if not scoped_name.startswith("P:"):
            return V3.bottom()

        rest = scoped_name.split(":", 1)[1]
        if "." not in rest:
            return V3.bottom()
        node_id, port_name = rest.split(".", 1)
        node = ir.nodes.get(node_id)
        if node is None:
            return V3.bottom()

        if port_name in node.input_meta:
            meta = node.input_meta[port_name]
            if meta.has_default:
                return V3.present(meta.default_value)
            return V3.absent()

        return V3.absent()

    def _non_monotone_vars(self, scc: List[str], ir: IRGraph) -> List[str]:
        bad: List[str] = []
        for node in scc:
            if node.startswith("V:"):
                name = node.split(":", 1)[1]
                var = ir.variables.get(name)
                if var and not var.is_monotone:
                    bad.append(name)
        return bad


class InitPass(Pass):
    name = "InitPass"

    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        strict = ir.config.get("mode") == "strict"
        if not strict:
            return

        for name, var in ir.variables.items():
            if not var.has_init:
                diag.error("INIT001", f"Variable '{name}' has no initial value", location=name)
            if var.is_delay_buffer and not var.has_init:
                diag.error(
                    "INIT002",
                    f"Delay-backed variable '{name}' must have an explicit default/init value.",
                    location=name,
                )


class NonZenoPass(Pass):
    name = "NonZenoPass"

    def run(self, ir: IRGraph, diag: DiagnosticSink) -> None:
        for node_id, node in ir.nodes.items():
            for reaction in node.reactions:
                overlap: Set[str] = set()
                for var_name in reaction.reads_vars:
                    if var_name in reaction.writes_vars:
                        var_meta = ir.variables.get(var_name)
                        if var_meta and getattr(var_meta, "is_delay_buffer", False):
                            continue
                        overlap.add(var_name)

                if overlap and not reaction.nonzeno_rank:
                    diag.error(
                        "ZEN001",
                        f"Reaction '{node_id}.{reaction.id}' depends on {sorted(overlap)} without non-zeno rank.",
                        location=node_id,
                    )
