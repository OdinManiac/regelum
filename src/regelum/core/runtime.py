from typing import Any, Dict, List, TypeVar, Set
from collections import defaultdict
from .types import NodeId
from .node import RawNode, Port, IntentContext
from .variables import Variable, Intent
from .values import ABSENT

T = TypeVar("T")

class ZenoRuntimeError(RuntimeError):
    """Raised when an instantaneous loop exceeds the allowed microsteps."""


class RuntimeIntentContext(IntentContext):
    def __init__(
        self,
        node_id: NodeId,
        port_state: Dict[Port, Any],
        edges: Dict[Port, Port],
        var_state: Dict[str, Any],
        intents: List[Intent[Any]],
        snapshot: Dict[Port, Any] | None = None,
    ):
        self.node_id = node_id
        self.port_state = port_state
        self.edges = edges
        self.var_state = var_state
        self.intents = intents
        self.snapshot = snapshot

    def read(self, port: Port) -> Any:
        # If port is an input, find connected output(s).
        sources = self.edges.get(port, [])
        
        if not sources:
            # No connection
            # Check if provided externally via inputs in run_tick
            if port in self.port_state:
                return self.port_state[port]
            
            if port.default is not None:
                return port.default
            return ABSENT

        if len(sources) == 1:
            # Single source
            source_port = sources[0]
            if self.snapshot is not None and source_port in self.snapshot:
                return self.snapshot[source_port]
            val = self.port_state.get(source_port, ABSENT)
            if val is ABSENT and port.default is not None:
                return port.default
            return val
        
        source_port = sources[-1]
        if self.snapshot is not None and source_port in self.snapshot:
            return self.snapshot[source_port]
        val = self.port_state.get(source_port, ABSENT)
        if val is ABSENT and port.default is not None:
            return port.default
        return val

    def write(self, port: Port, value: Any) -> None:
        self.port_state[port] = value

    def read_var(self, var: Variable[T]) -> T:
        # Return committed value or init
        if var.name in self.var_state:
            return self.var_state[var.name]
        return var.init

    def write_var(self, var: Variable[T], value: T) -> None:
        self.intents.append(Intent(var, self.node_id, value))

class GraphRuntime:
    def __init__(self):
        self.nodes: Dict[NodeId, RawNode] = {}
        self.edges: Dict[Port, List[Port]] = defaultdict(list)
        self.schedule: List[List[NodeId]] = [] # List of SCCs
        self.port_state: Dict[Port, Any] = {}
        self.var_state: Dict[str, Any] = {} # Variable.name -> Value
        self.max_microsteps = 20
        self.current_time = 0.0
        self.tickwise_mode = False
        self._tickwise_outputs: Dict[Port, Any] = {}

    def add_node(self, node: RawNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Node with id {node.id} already exists")
        self.nodes[node.id] = node
        node.bind_runtime(self) # Bind runtime to node and its ports

    def connect(self, src: Port, dst: Port) -> None:
        self.edges[dst].append(src)

    def build_schedule(self) -> None:
        # Build two graphs:
        # - adj_full: all dependencies for topological ordering.
        # - adj_scc: dependencies excluding nodes that declare no_instant_loop, to prevent
        #   them from participating in instantaneous cycles.
        adj_full: Dict[NodeId, List[NodeId]] = defaultdict(list)
        adj_scc: Dict[NodeId, List[NodeId]] = defaultdict(list)
        for dst_port, src_ports in self.edges.items():
            for src_port in src_ports:
                dst_nid = dst_port.node_id
                src_nid = src_port.node_id
                if not dst_nid or not src_nid or dst_nid == src_nid:
                    continue
                if getattr(src_port, "is_delay_output", False):
                    # Delay outputs deliver previous-tick values, so consumers must run
                    # before producers overwrite the buffer this tick.
                    adj_full[dst_nid].append(src_nid)
                    continue
                adj_full[src_nid].append(dst_nid)
                if not self.nodes[dst_nid]._no_instant_loop:
                    adj_scc[src_nid].append(dst_nid)

        for node_id in self.nodes:
            adj_full.setdefault(node_id, [])
            adj_scc.setdefault(node_id, [])

        # Tarjan on adj_scc to keep no_instant_loop nodes as SCC boundaries.
        visited: Set[NodeId] = set()
        stack: List[NodeId] = []
        on_stack: Set[NodeId] = set()
        ids: Dict[NodeId, int] = {}
        low: Dict[NodeId, int] = {}
        id_counter = 0
        sccs: List[List[NodeId]] = []

        def dfs(at: NodeId):
            nonlocal id_counter
            stack.append(at)
            on_stack.add(at)
            visited.add(at)
            ids[at] = low[at] = id_counter
            id_counter += 1

            for to in adj_scc[at]:
                if to not in visited:
                    dfs(to)
                    low[at] = min(low[at], low[to])
                elif to in on_stack:
                    low[at] = min(low[at], ids[to])

            if ids[at] == low[at]:
                scc: List[NodeId] = []
                while stack:
                    node = stack.pop()
                    on_stack.remove(node)
                    scc.append(node)
                    if node == at:
                        break
                sccs.append(scc)

        for node_id in self.nodes:
            if node_id not in visited:
                dfs(node_id)

        # Map node -> SCC index
        scc_index: Dict[NodeId, int] = {}
        for idx, comp in enumerate(sccs):
            for nid in comp:
                scc_index[nid] = idx

        # Condensation graph using full dependencies to preserve order.
        cond_adj: Dict[int, Set[int]] = defaultdict(set)
        indeg: Dict[int, int] = defaultdict(int)
        for src, dsts in adj_full.items():
            for dst in dsts:
                s_src = scc_index[src]
                s_dst = scc_index[dst]
                if s_src == s_dst:
                    continue
                if s_dst not in cond_adj[s_src]:
                    cond_adj[s_src].add(s_dst)
                    indeg[s_dst] += 1
        for idx in range(len(sccs)):
            indeg.setdefault(idx, 0)

        # Kahn topological order on SCC DAG.
        ready = [idx for idx, deg in indeg.items() if deg == 0]
        ready.sort()
        topo_scc: List[int] = []
        while ready:
            current = ready.pop(0)
            topo_scc.append(current)
            for nbr in cond_adj.get(current, ()):
                indeg[nbr] -= 1
                if indeg[nbr] == 0:
                    ready.append(nbr)
                    ready.sort()

        if len(topo_scc) != len(sccs):
            topo_scc = list(range(len(sccs)))

        self.schedule = [sccs[idx] for idx in topo_scc]

    def _prefill_delay_outputs(self) -> None:
        for node in self.nodes.values():
            state_vars = getattr(node, "_state_vars", None)
            if not state_vars:
                continue
            for port in node.outputs.values():
                delay_state = getattr(port, "delay_state_name", None)
                if not delay_state:
                    continue
                if delay_state not in state_vars:
                    continue
                var = state_vars[delay_state]
                value = self.var_state.get(var.name, var.init)
                self.port_state[port] = value

    def run_step(self) -> None:
        for scc in self.schedule:
            for node_id in scc:
                node = self.nodes[node_id]
                ctx = RuntimeIntentContext(
                    node_id,
                    self.port_state,
                    self.edges,
                    self.var_state,
                    [] 
                )
                node.step(ctx)

    def run_tick(self, inputs: Dict[Port, Any] | None = None, dt: float | None = None) -> None:
        # Stage 6: Clear ports at start of tick.
        snapshot: Dict[Port, Any] | None = None
        if self.tickwise_mode:
            snapshot = dict(self._tickwise_outputs)
        self.port_state.clear()
        self._prefill_delay_outputs()

        if dt is not None:
            for node in self.nodes.values():
                for name, port in node.inputs.items():
                    if name == "dt":
                        self.port_state[port] = dt
        
        # Apply external inputs if provided
        if inputs:
            self.port_state.update(inputs)
        
        intents: List[Intent[Any]] = []
        
        # 1. Propose Phase
        self._propose_phase(intents, snapshot)
        
        # 2. Resolve Phase
        updates = self._resolve_phase(intents)
        
        # 3. Commit Phase
        self._commit_phase(updates)
        if self.tickwise_mode:
            outputs: Dict[Port, Any] = {}
            for node in self.nodes.values():
                for port in node.outputs.values():
                    val = self.port_state.get(port, ABSENT)
                    outputs[port] = val
            self._tickwise_outputs = outputs
        if dt is not None:
            self.current_time += dt

    def _propose_phase(self, intents: List[Intent[Any]], snapshot: Dict[Port, Any] | None) -> None:
        for scc in self.schedule:
            if len(scc) == 1 and not self._has_self_loop(scc[0]):
                self._run_node(scc[0], intents, snapshot)
            else:
                self._run_scc_loop(scc, intents, snapshot)

    def _has_self_loop(self, node_id: NodeId) -> bool:
        for dst_port, src_ports in self.edges.items():
            if dst_port.node_id == node_id:
                for src_port in src_ports:
                    if src_port.node_id == node_id:
                        return True
        return False

    def _run_node(self, node_id: NodeId, intents: List[Intent[Any]], snapshot: Dict[Port, Any] | None) -> None:
        node = self.nodes[node_id]
        ctx = RuntimeIntentContext(
            node_id, 
            self.port_state, 
            self.edges,
            self.var_state, 
            intents,
            snapshot,
        )
        node.step(ctx)
    
    def _run_node_with_state(
        self,
        node_id: NodeId,
        intents: List[Intent[Any]],
        var_state: Dict[str, Any],
        snapshot: Dict[Port, Any] | None,
    ) -> None:
        node = self.nodes[node_id]
        ctx = RuntimeIntentContext(
            node_id, 
            self.port_state, 
            self.edges,
            var_state, 
            intents,
            snapshot,
        )
        node.step(ctx)

    def _run_scc_loop(
        self,
        scc: List[NodeId],
        global_intents: List[Intent[Any]],
        snapshot: Dict[Port, Any] | None,
    ) -> None:
        prev_outputs: Dict[Port, Any] = {}
        working_vars: Dict[str, Any] = dict(self.var_state)
        last_intents: List[Intent[Any]] = []
        limit = self._scc_limit(scc)
        for _ in range(limit):
            current_intents: List[Intent[Any]] = []
            changed = False
            before_vars = dict(working_vars)
            
            for node_id in scc:
                self._run_node_with_state(node_id, current_intents, working_vars, snapshot)
                
                node = self.nodes[node_id]
                for port in node.outputs.values():
                    new_val = self.port_state.get(port, ABSENT)
                    old_val = prev_outputs.get(port, ABSENT)
                    
                    if new_val != old_val:
                        changed = True
                        prev_outputs[port] = new_val
            
            updates = self._resolve_phase(current_intents)
            for name, val in updates.items():
                prev_val = working_vars.get(name, ABSENT)
                if prev_val != val:
                    changed = True
                    working_vars[name] = val
            
            last_intents = current_intents
            if not changed and before_vars == working_vars:
                global_intents.extend(last_intents)
                return
                
        raise ZenoRuntimeError(f"Instantaneous loop {scc} exceeded {limit} microsteps without convergence.")

    def _resolve_phase(self, intents: List[Intent[Any]]) -> Dict[str, Any]:
        grouped: Dict[Variable[Any], List[Intent[Any]]] = defaultdict(list)
        for intent in intents:
            grouped[intent.variable].append(intent)
            
        updates: Dict[str, Any] = {}
        
        for var, var_intents in grouped.items():
            merged_val = var.write_policy.merge(var_intents)
            updates[var.name] = merged_val
            
        return updates

    def _commit_phase(self, updates: Dict[str, Any]) -> None:
        self.var_state.update(updates)

    def _scc_limit(self, scc: List[NodeId]) -> int:
        limit = self.max_microsteps
        for node_id in scc:
            node = self.nodes[node_id]
            reactions = getattr(node, "reactions", None)
            if not reactions:
                continue
            for reaction in reactions:
                rank_limit = getattr(reaction, "nonzeno_limit", None)
                if rank_limit is not None:
                    limit = min(limit, rank_limit)
        return limit
