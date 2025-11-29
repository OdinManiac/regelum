from __future__ import annotations

from typing import Dict, Generic, List, TypeVar

from .types import NodeId

T = TypeVar("T")

INTEGRATOR_EULER = "euler"
INTEGRATOR_RK4 = "rk4"
DEFAULT_MAX_STEP = 0.01


class ContinuousState(Generic[T]):
    def __init__(self, init: T):
        self.init = init
        self.name: str | None = None

    def __set_name__(self, owner, name: str) -> None:
        self.name = name


class ContinuousNode:
    """
    Minimal continuous-time node: dx/dt = f(t, x, u), y = h(t, x, u).
    Designed for pure CT subgraphs (Stage 10A from continuous.md).
    """

    integrator: str = INTEGRATOR_RK4
    max_step: float = DEFAULT_MAX_STEP

    def __init__(self, node_id: str):
        self.id = NodeId(node_id)
        self._states: Dict[str, ContinuousState[T]] = {}
        self._build_states()

    def _build_states(self) -> None:
        seen: set[str] = set()
        for cls in reversed(self.__class__.mro()):
            for name, attr in cls.__dict__.items():
                if name in seen:
                    continue
                if isinstance(attr, ContinuousState):
                    attr.name = name
                    self._states[name] = attr
                    seen.add(name)

    def derivative(self, t: float, state: Dict[str, float], inputs: Dict[str, float]) -> Dict[str, float]:
        raise NotImplementedError

    def outputs(self, t: float, state: Dict[str, float], inputs: Dict[str, float]) -> Dict[str, float]:
        return {}

    def initial_state(self) -> Dict[str, float]:
        state: Dict[str, float] = {}
        for name, descr in self._states.items():
            state[name] = descr.init
        return state

    def as_hybrid(
        self,
        *,
        name: str | None = None,
        dt: float | None = None,
        hold_init: float | Dict[str, float] = 0.0,
        u_name: str = "u",
        state_name: str = "state",
        y_name: str = "y",
    ):
        """
        Convenience: wrap this continuous node into a hybrid adapter with built-in ZOH semantics.
        Returns a node ready to add to a discrete pipeline with ports: u (input), y/state (outputs), dt (input).
        """
        from .hybrid_adapters import HybridContinuousWrapper  # Local import to avoid cycle

        wrapper_id = name if name is not None else str(self.id)
        default_dt = dt if dt is not None else self.max_step
        return HybridContinuousWrapper(
            wrapper_id,
            self,
            default_dt=default_dt,
            hold_init=hold_init,
            u_name=u_name,
            state_name=state_name,
            y_name=y_name,
        )

    def _state_names(self) -> List[str]:
        return list(self._states.keys())


class ContinuousRuntime:
    """
    Lightweight runtime that advances pure continuous nodes in time
    using a fixed-step integrator (Euler or RK4).
    """

    def __init__(self):
        self.nodes: Dict[NodeId, ContinuousNode] = {}
        self.state: Dict[NodeId, Dict[str, float]] = {}
        self.last_inputs: Dict[NodeId, Dict[str, float]] = {}
        self.t: float = 0.0
        self.traces: Dict[NodeId, List[tuple[float, Dict[str, float]]]] = {}

    def add_node(self, node: ContinuousNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Continuous node '{node.id}' already added")
        self.nodes[node.id] = node
        init = node.initial_state()
        self.state[node.id] = init
        self.last_inputs[node.id] = {}
        self.traces[node.id] = [(self.t, node.outputs(self.t, init, {}))]

    def _check_derivative_keys(self, node: ContinuousNode, deriv: Dict[str, float]) -> None:
        expected = set(node._state_names())
        actual = set(deriv.keys())
        if expected != actual:
            missing = expected - actual
            extra = actual - expected
            details = []
            if missing:
                details.append(f"missing {sorted(missing)}")
            if extra:
                details.append(f"unexpected {sorted(extra)}")
            joined = "; ".join(details)
            raise ValueError(f"Derivative for node '{node.id}' must define all states ({joined})")

    def _combine(self, base: Dict[str, float], delta: Dict[str, float], scale: float) -> Dict[str, float]:
        combined: Dict[str, float] = {}
        for name, value in base.items():
            combined[name] = value + scale * delta[name]
        return combined

    def _derivative(self, node: ContinuousNode, t: float, state: Dict[str, float], inputs: Dict[str, float]) -> Dict[str, float]:
        deriv = node.derivative(t, state, inputs)
        self._check_derivative_keys(node, deriv)
        return deriv

    def _euler_step(self, node: ContinuousNode, t: float, dt: float, state: Dict[str, float], inputs: Dict[str, float]) -> Dict[str, float]:
        deriv = self._derivative(node, t, state, inputs)
        return self._combine(state, deriv, dt)

    def _rk4_step(self, node: ContinuousNode, t: float, dt: float, state: Dict[str, float], inputs: Dict[str, float]) -> Dict[str, float]:
        k1 = self._derivative(node, t, state, inputs)
        k2_state = self._combine(state, k1, dt * 0.5)
        k2 = self._derivative(node, t + dt * 0.5, k2_state, inputs)
        k3_state = self._combine(state, k2, dt * 0.5)
        k3 = self._derivative(node, t + dt * 0.5, k3_state, inputs)
        k4_state = self._combine(state, k3, dt)
        k4 = self._derivative(node, t + dt, k4_state, inputs)

        updated: Dict[str, float] = {}
        for name, value in state.items():
            delta = (
                k1[name]
                + 2.0 * k2[name]
                + 2.0 * k3[name]
                + k4[name]
            )
            updated[name] = value + (dt / 6.0) * delta
        return updated

    def step(self, dt: float, controls: Dict[NodeId, Dict[str, float]] | None = None) -> None:
        if dt <= 0.0:
            raise ValueError("dt must be positive for continuous integration")
        if not self.nodes:
            return
        control_map = controls if controls is not None else {}
        next_t = self.t + dt
        new_states: Dict[NodeId, Dict[str, float]] = {}
        for node_id, node in self.nodes.items():
            inputs = control_map.get(node_id, self.last_inputs.get(node_id, {}))
            self.last_inputs[node_id] = inputs
            if node.max_step > 0.0 and dt > node.max_step:
                raise ValueError(
                    f"dt={dt} exceeds max_step={node.max_step} for node '{node_id}'"
                )
            current_state = self.state[node_id]
            if node.integrator == INTEGRATOR_EULER:
                updated = self._euler_step(node, self.t, dt, current_state, inputs)
            else:
                updated = self._rk4_step(node, self.t, dt, current_state, inputs)
            new_states[node_id] = updated
        for node_id, updated in new_states.items():
            self.state[node_id] = updated
            node = self.nodes[node_id]
            last_input = self.last_inputs.get(node_id, {})
            self.traces[node_id].append((next_t, node.outputs(next_t, updated, last_input)))
        self.t = next_t

    def run(self, total_time: float, dt: float, controls: Dict[NodeId, Dict[str, float]] | None = None) -> None:
        if total_time <= 0.0:
            raise ValueError("total_time must be positive for continuous integration")
        remaining = total_time
        while remaining > 0.0:
            step_dt = dt if remaining >= dt else remaining
            self.step(step_dt, controls)
            remaining -= step_dt

    def outputs(self, node_id: NodeId) -> Dict[str, float]:
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found in continuous runtime")
        node = self.nodes[node_id]
        inputs = self.last_inputs.get(node_id, {})
        return node.outputs(self.t, self.state[node_id], inputs)

    def get_state(self, node_id: NodeId) -> Dict[str, float]:
        if node_id not in self.state:
            raise KeyError(f"Node '{node_id}' not found in continuous runtime")
        return dict(self.state[node_id])
