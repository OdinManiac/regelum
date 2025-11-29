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
    def __init__(self, 
                 node_id: NodeId,
                 port_state: Dict[Port, Any], 
                 edges: Dict[Port, Port],
                 var_state: Dict[str, Any], # map variable name to value
                 intents: List[Intent[Any]]):
        self.node_id = node_id
        self.port_state = port_state
        self.edges = edges
        self.var_state = var_state
        self.intents = intents

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
            val = self.port_state.get(source_port, ABSENT)
            if val is ABSENT and port.default is not None:
                return port.default
            return val
        
        source_port = sources[-1]
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

    def add_node(self, node: RawNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Node with id {node.id} already exists")
        self.nodes[node.id] = node
        node.bind_runtime(self) # Bind runtime to node and its ports

    def connect(self, src: Port, dst: Port) -> None:
        self.edges[dst].append(src)

    def build_schedule(self) -> None:
        # Tarjan's Algorithm to find SCCs
        adj: Dict[NodeId, List[NodeId]] = defaultdict(list)
        for dst_port, src_ports in self.edges.items():
            for src_port in src_ports:
                dst_nid = dst_port.node_id
                src_nid = src_port.node_id
                if dst_nid and self.nodes[dst_nid]._no_instant_loop:
                    continue
                if dst_nid and src_nid and dst_nid != src_nid:
                    adj[src_nid].append(dst_nid)

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

            for to in adj[at]:
                if to not in visited:
                    dfs(to)
                    low[at] = min(low[at], low[to])
                elif to in on_stack:
                    low[at] = min(low[at], ids[to])

            if ids[at] == low[at]:
                scc = []
                while stack:
                    node = stack.pop()
                    on_stack.remove(node)
                    scc.append(node)
                    if node == at: break
                sccs.append(scc) # sccs are found in reverse topological order

        for node_id in self.nodes:
            if node_id not in visited:
                dfs(node_id)

        self.schedule = sccs 
        self.schedule.reverse()

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
        self._propose_phase(intents)
        
        # 2. Resolve Phase
        updates = self._resolve_phase(intents)
        
        # 3. Commit Phase
        self._commit_phase(updates)
        if dt is not None:
            self.current_time += dt

    def _propose_phase(self, intents: List[Intent[Any]]) -> None:
        for scc in self.schedule:
            if len(scc) == 1 and not self._has_self_loop(scc[0]):
                self._run_node(scc[0], intents)
            else:
                self._run_scc_loop(scc, intents)

    def _has_self_loop(self, node_id: NodeId) -> bool:
        for dst_port, src_ports in self.edges.items():
            if dst_port.node_id == node_id:
                for src_port in src_ports:
                    if src_port.node_id == node_id:
                        return True
        return False

    def _run_node(self, node_id: NodeId, intents: List[Intent[Any]]) -> None:
        node = self.nodes[node_id]
        ctx = RuntimeIntentContext(
            node_id, 
            self.port_state, 
            self.edges,
            self.var_state, 
            intents
        )
        node.step(ctx)
    
    def _run_node_with_state(self, node_id: NodeId, intents: List[Intent[Any]], var_state: Dict[str, Any]) -> None:
        node = self.nodes[node_id]
        ctx = RuntimeIntentContext(
            node_id, 
            self.port_state, 
            self.edges,
            var_state, 
            intents
        )
        node.step(ctx)

    def _run_scc_loop(self, scc: List[NodeId], global_intents: List[Intent[Any]]) -> None:
        prev_outputs: Dict[Port, Any] = {}
        working_vars: Dict[str, Any] = dict(self.var_state)
        last_intents: List[Intent[Any]] = []
        limit = self._scc_limit(scc)
        for _ in range(limit):
            current_intents: List[Intent[Any]] = []
            changed = False
            before_vars = dict(working_vars)
            
            for node_id in scc:
                self._run_node_with_state(node_id, current_intents, working_vars)
                
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
