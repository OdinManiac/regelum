from typing import Any, Dict, List, TypeVar, Set
from collections import deque, defaultdict
from .types import NodeId
from .node import RawNode, Port, IntentContext
from .variables import Variable, Intent
from .values import ABSENT

T = TypeVar("T")

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
        
        # Multiple sources -> Merge?
        # For now, strictly return the first one or handle differently?
        # If we are here, the Compiler might have warned/errored.
        # Runtime behavior: Non-deterministic or LWW (last one added).
        # Let's just pick the last one to simulate LWW if strict mode didn't block it.
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
                if dst_port.node_id and src_port.node_id and dst_port.node_id != src_port.node_id:
                    adj[src_port.node_id].append(dst_port.node_id)

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

        self.schedule = sccs # Reverse topological order (leafs first? No, Tarjan returns reverse topo)
        # Actually Tarjan returns SCCs in reverse topological order (leaves first).
        # For execution we usually want Roots first (Source -> Sink).
        # So we should reverse the list.
        self.schedule.reverse()

    def run_step(self) -> None:
        # Legacy run_step
        # Iterate flattened schedule
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

    def run_tick(self, inputs: Dict[Port, Any] = None) -> None:
        # Stage 6: Clear ports at start of tick.
        self.port_state.clear()
        
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
            self.edges, # Pass the dict(list)
            self.var_state, 
            intents
        )
        node.step(ctx)

    def _run_scc_loop(self, scc: List[NodeId], global_intents: List[Intent[Any]]) -> None:
        # Fixed-point iteration
        # We run until output ports of SCC nodes stabilize.
        
        # Optimization: Only track ports belonging to nodes in SCC?
        # For simplicity, we re-run and capture local intents.
        
        prev_outputs: Dict[Port, Any] = {}
        
        # Max iterations
        for _ in range(20):
            current_intents: List[Intent[Any]] = []
            changed = False
            
            for node_id in scc:
                self._run_node(node_id, current_intents)
                
                # Check outputs change
                node = self.nodes[node_id]
                for port in node.outputs.values():
                    new_val = self.port_state.get(port, ABSENT)
                    old_val = prev_outputs.get(port, ABSENT)
                    
                    if new_val != old_val:
                        changed = True
                        prev_outputs[port] = new_val
            
            if not changed:
                global_intents.extend(current_intents)
                return
                
        # If not converged, we warn but proceed with latest values (or raise Zeno error at runtime?)
        # discussion.md suggests Zeno check is compile time. Runtime enforces limit.
        global_intents.extend(current_intents)
        # print(f"WARNING: SCC {scc} did not converge in 20 steps.")

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
