from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

import pytest

os.environ["RG_DISABLE_FILE_LOGS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipes.router_fixed_point import INF, _default_graph, build_router_pipeline
from rg_compiler.core.runtime import ZenoRuntimeError


def _bellman_ford(graph: Dict[str, Dict[str, float]], target: str) -> Dict[str, float]:
    distances: Dict[str, float] = {name: INF for name in graph}
    distances[target] = 0.0
    for _ in range(max(1, len(graph))):
        updated = False
        for node, edges in graph.items():
            best = distances[node]
            for neighbor, cost in edges.items():
                candidate = distances[neighbor] + cost
                if candidate < best:
                    best = candidate
            if best != distances[node]:
                distances[node] = best
                updated = True
        if not updated:
            break
    return distances


def _read_distances(pipe, routers):
    return {name: pipe.runtime.port_state[node.outputs[f"dist_{name}"]] for name, node in routers.items()}


def test_router_fixed_point_default_target_converges():
    graph = _default_graph()
    expected = _bellman_ford(graph, "E")

    pipe, routers = build_router_pipeline(graph=graph, target="E", visualize=False)
    pipe.run(ticks=1)

    assert _read_distances(pipe, routers) == pytest.approx(expected)


def test_router_fixed_point_handles_unfavorable_schedule():
    graph = _default_graph()
    expected = _bellman_ford(graph, "A")

    pipe, routers = build_router_pipeline(graph=graph, target="A", visualize=False)
    pipe.run(ticks=1)

    assert pipe.runtime.max_microsteps >= len(graph)
    assert _read_distances(pipe, routers) == pytest.approx(expected)


def test_router_fixed_point_requires_sufficient_microsteps():
    graph = _default_graph()
    pipe, _ = build_router_pipeline(graph=graph, target="A", visualize=False, max_microsteps=2)

    with pytest.raises(ZenoRuntimeError):
        pipe.run(ticks=1)


def test_router_tickwise_propagates_per_tick():
    graph = _default_graph()
    pipe, routers = build_router_pipeline(graph=graph, target="E", visualize=False, tickwise=True)

    wave = []
    for _ in range(4):
        pipe.run(ticks=1)
        wave.append(_read_distances(pipe, routers))
    expected = _bellman_ford(graph, "E")

    # Tick 1: only direct neighbors of E update
    assert wave[0]["E"] == pytest.approx(0.0)
    assert wave[0]["D"] == pytest.approx(2.0)
    assert wave[0]["C"] == pytest.approx(8.0)
    assert wave[0]["A"] == pytest.approx(20.0)
    assert wave[0]["B"] > INF / 2

    # Tick 2: length-2 paths propagate
    assert wave[1]["B"] == pytest.approx(6.0)
    assert wave[1]["A"] == pytest.approx(10.0)

    # Tick 3: reach the fixed point
    assert wave[2] == pytest.approx(expected)
    assert wave[3] == pytest.approx(expected)
