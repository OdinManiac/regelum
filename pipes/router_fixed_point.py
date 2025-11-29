from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple

import pygame

from regelum.api import Pipeline
from regelum.core.contracts import contract
from regelum.core.core_node import CoreNode, Input, Output, reaction
from regelum.core.dsl import BinOp, Const, Expr
from regelum.core.ext_node import ExtNode
from regelum.core.node import Context, Port

INF = 1e9


def _build_router_class(node_id: str, edges: Dict[str, float], is_target: bool, *, tickwise: bool):
    neighbors = list(edges.keys())
    inputs: Dict[str, Input[float]] = {f"dist_{n}": Input[float](default=INF) for n in neighbors}
    output_name = f"dist_{node_id}"
    attrs: Dict[str, object] = {**inputs, output_name: Output[float](), "__module__": __name__}

    # Build reaction source without loops to satisfy CoreNode lint.
    params = ", ".join(inputs.keys())
    expr_lines: list[str] = []
    if is_target:
        expr_lines.append("    return Const(0.0)")
    elif tickwise:
        terms = [f"BinOp('+', dist_{n}, Const({edges[n]}))" for n in neighbors]
        expr_lines.append(f"    return {_min_chain_str(terms)}")
    else:
        terms = [f"BinOp('+', dist_{n}, Const({edges[n]}))" for n in neighbors]
        expr_lines.append(f"    return {_min_chain_str(terms)}")

    src = f"def propagate(self, {params}):\n" + "\n".join(expr_lines) + "\n"
    filename = f"<router_{node_id}>"
    compiled = compile(src, filename, "exec")
    import linecache

    linecache.cache[filename] = (len(src), None, src.splitlines(True), filename)
    scope = {"BinOp": BinOp, "Const": Const, "INF": INF}
    exec(compiled, scope)
    attrs["propagate"] = reaction(scope["propagate"])
    cls = type(f"Router{node_id}", (CoreNode,), attrs)
    return cls


def _min_chain_str(terms: list[str]) -> str:
    if not terms:
        return "Const(INF)"
    nested = terms[0]
    for term in terms[1:]:
        nested = f"BinOp('min', {nested}, {term})"
    return nested


def build_router_node(node_id: str, edges: Dict[str, float], is_target: bool, *, tickwise: bool) -> CoreNode:
    cls = _build_router_class(node_id, edges, is_target, tickwise=tickwise)
    node = cls(node_id)  # type: ignore[call-arg]
    return node


class GraphVisualizer(ExtNode):
    def __init__(
        self,
        node_id: str,
        coords: Dict[str, Tuple[float, float]],
        graph: Dict[str, Dict[str, float]],
        *,
        target: str,
        tickwise: bool,
    ):
        super().__init__(node_id)
        self._coords = coords
        self._graph = graph
        self._target = target
        self._tickwise = tickwise
        for name in coords:
            self.add_input(f"dist_{name}", default=INF)
        self._width = 900
        self._height = 700
        self._radius = 18
        self._tick = 0
        self._prev: Dict[str, float] = {}
        self._bg_color = (12, 12, 16)
        self._edge_color = (70, 80, 95)
        self._edge_active = (255, 170, 80)
        self._node_hi = (255, 230, 120)
        self._text = (230, 230, 235)
        self._inf_color = (80, 80, 90)

    @contract(deterministic=True, no_side_effects=False, no_instant_loop=False)
    def step(self, ctx: Context):
        pygame.init()
        screen = pygame.display.get_surface()
        if screen is None:
            screen = pygame.display.set_mode((self._width, self._height))
        screen.fill(self._bg_color)
        font = pygame.font.SysFont("menlo", 16)
        small = pygame.font.SysFont("menlo", 14)

        distances: Dict[str, float] = {}
        for name in self._coords:
            val = ctx.read(self.inputs[f"dist_{name}"])
            distances[name] = val

        self._draw_edges(screen, distances, small)
        self._draw_nodes(screen, distances, font)
        self._draw_overlay(screen, small)

        self._prev = distances
        self._tick += 1
        pygame.display.flip()

    def _draw_edges(self, screen: pygame.Surface, distances: Dict[str, float], font: pygame.font.Font):
        for src, dsts in self._graph.items():
            sx, sy = self._coords[src]
            for dst, cost in dsts.items():
                dx, dy = self._coords[dst]
                start = (int(sx * self._width), int(sy * self._height))
                end = (int(dx * self._width), int(dy * self._height))
                active = distances[src] + cost <= distances[dst] + 1e-6
                color = self._edge_active if active else self._edge_color
                width = 3 if active else 2
                pygame.draw.line(screen, color, start, end, width)

                mx = (start[0] + end[0]) // 2
                my = (start[1] + end[1]) // 2
                label = font.render(f"{cost:g}", True, self._text)
                rect = label.get_rect(center=(mx, my))
                screen.blit(label, rect)

    def _draw_nodes(self, screen: pygame.Surface, distances: Dict[str, float], font: pygame.font.Font):
        scale = max(1.0, max((d for d in distances.values() if d < INF / 2), default=1.0))
        for name, (x, y) in self._coords.items():
            px = int(self._width * x)
            py = int(self._height * y)
            val = distances.get(name, INF)
            updated = name in self._prev and val != self._prev[name]
            fill = self._color_for_value(val, scale)
            border = self._node_hi if updated else self._text
            pygame.draw.circle(screen, border, (px, py), self._radius + 2)
            pygame.draw.circle(screen, fill, (px, py), self._radius)
            label = font.render(f"{name}:{self._fmt(val)}", True, self._bg_color)
            rect = label.get_rect(center=(px, py))
            screen.blit(label, rect)

    def _draw_overlay(self, screen: pygame.Surface, font: pygame.font.Font):
        mode = "tickwise" if self._tickwise else "fixed-point"
        lines = [
            f"tick {self._tick}",
            f"target {self._target}  | mode: {mode}",
            "SPACE pause/resume   N step   Q quit",
        ]
        for idx, text in enumerate(lines):
            label = font.render(text, True, self._text)
            screen.blit(label, (16, 16 + idx * 20))

    def _color_for_value(self, val: float, scale: float) -> Tuple[int, int, int]:
        if val >= INF / 2:
            return self._inf_color
        t = min(max(val / scale, 0.0), 1.0)
        cold = (120, 210, 255)
        warm = (255, 120, 90)
        return (
            int(cold[0] + (warm[0] - cold[0]) * t),
            int(cold[1] + (warm[1] - cold[1]) * t),
            int(cold[2] + (warm[2] - cold[2]) * t),
        )

    @staticmethod
    def _fmt(val: float) -> str:
        if val >= INF / 2:
            return "∞"
        return f"{val:.1f}"


def _default_graph() -> Dict[str, Dict[str, float]]:
    return {
        "A": {"B": 3.0, "C": 2.0, "E": 20.0},
        "B": {"A": 3.0, "C": 1.0, "D": 4.0},
        "C": {"A": 2.0, "B": 1.0, "D": 5.0, "E": 8.0},
        "D": {"B": 4.0, "C": 5.0, "E": 2.0},
        "E": {"A": 20.0, "C": 8.0, "D": 2.0},
    }


def _demo_graph() -> Dict[str, Dict[str, float]]:
    # Larger undirected-ish graph to showcase wave propagation.
    return {
        "A": {"B": 3.0, "C": 2.0, "D": 4.0, "F": 9.0},
        "B": {"A": 3.0, "C": 1.0, "G": 6.0, "H": 7.0},
        "C": {"A": 2.0, "B": 1.0, "D": 5.0, "I": 4.0},
        "D": {"A": 4.0, "C": 5.0, "E": 2.0, "J": 5.0},
        "E": {"D": 2.0, "K": 3.0, "L": 7.0},
        "F": {"A": 9.0, "G": 4.0, "M": 6.0},
        "G": {"B": 6.0, "F": 4.0, "H": 2.0, "N": 5.0},
        "H": {"B": 7.0, "G": 2.0, "I": 3.0, "O": 9.0},
        "I": {"C": 4.0, "H": 3.0, "J": 2.0},
        "J": {"D": 5.0, "I": 2.0, "K": 4.0},
        "K": {"E": 3.0, "J": 4.0, "L": 2.0},
        "L": {"E": 7.0, "K": 2.0, "O": 4.0},
        "M": {"F": 6.0, "N": 3.0},
        "N": {"G": 5.0, "M": 3.0, "O": 5.0},
        "O": {"H": 9.0, "L": 4.0, "N": 5.0},
    }


def _circle_layout(nodes: Iterable[str]) -> Dict[str, Tuple[float, float]]:
    names = list(nodes)
    n = len(names)
    coords: Dict[str, Tuple[float, float]] = {}
    for idx, name in enumerate(names):
        angle = 2 * math.pi * idx / max(1, n)
        coords[name] = (0.5 + 0.35 * math.cos(angle), 0.5 + 0.35 * math.sin(angle))
    return coords


def _microstep_budget(graph: Dict[str, Dict[str, float]]) -> int:
    # Bellman-Ford style relaxation needs at most one pass per vertex to push
    # the target's distance along the longest simple path.
    return max(1, len(graph))


def build_router_pipeline(
    graph: Dict[str, Dict[str, float]] | None = None,
    *,
    target: str = "E",
    visualize: bool = False,
    max_microsteps: int | None = None,
    tickwise: bool = False,
) -> Tuple[Pipeline, Dict[str, CoreNode]]:
    g = _default_graph() if graph is None else graph
    if target not in g:
        raise ValueError(f"Target '{target}' not found in graph")

    ordered_names = [target] + [name for name in g if name != target]
    routers: Dict[str, CoreNode] = {}
    for name in ordered_names:
        routers[name] = build_router_node(name, g[name], is_target=(name == target), tickwise=tickwise)

    pipe = Pipeline(mode="strict")
    for node in routers.values():
        if tickwise:
            node._no_instant_loop = True
        pipe.add(node)

    if tickwise:
        initial_outputs: Dict[Port, float] = {}
        for name, node in routers.items():
            out_port = node.outputs[f"dist_{name}"]
            initial_outputs[out_port] = 0.0 if name == target else INF
        pipe.runtime.tickwise_mode = True
        pipe.runtime._tickwise_outputs = initial_outputs

    if visualize:
        coords = _circle_layout(g.keys())
        vis = GraphVisualizer("GraphVis", coords, g, target=target, tickwise=tickwise)
        pipe.add(vis)

    pipe.auto_wire(strict=True)
    if max_microsteps is not None:
        limit = max_microsteps
    elif tickwise:
        limit = 1
    else:
        limit = _microstep_budget(g)
    pipe.runtime.max_microsteps = limit
    return pipe, routers


def run_demo() -> None:
    big = _demo_graph()
    pipe, _ = build_router_pipeline(graph=big, target="O", visualize=True, tickwise=True)
    if not pipe.compile():
        return
    pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((900, 700))
    pygame.display.set_caption("Router fixed point (SPACE play/pause, N step, Q quit)")
    clock = pygame.time.Clock()
    paused = True
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    paused = not paused
                if event.key == pygame.K_n:
                    pipe.run(ticks=1)
                if event.key == pygame.K_q:
                    return
        if not paused:
            pipe.run(ticks=1)
        clock.tick(10)


if __name__ == "__main__":
    run_demo()
