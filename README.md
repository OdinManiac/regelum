# Regelum Compiler

Reactive pipeline compiler with staged formal guarantees (structural, causality, non-Zeno, SDF, hybrid CT).

## Status

- Stages 0-12 implemented: Core runtime + DSL, analyses (structural/causality/init/non-Zeno/SDF), contracts for Ext/Raw, wiring DSL (`>>`), Pipeline facade, and auto-wiring.
- Python 3.13+. Use `uv` for installs/runs (`uv sync`, `uv run ...`).

## What the compiler guarantees today

- **Structural sanity**: unconnected inputs and fan-in>1 are errors (`STRUCT001/002` in `compiler/passes.py`); type mismatches are surfaced as `TYPE001` warnings.
- **Write policies**: multiwriter conflicts are rejected for `ErrorPolicy` (`WRITE001`). `LWWPolicy` multiwriters warn in `best_effort/pragmatic` and fail in `strict` (`WRITE002`). Policies come from `core/variables.py`.
- **Causality & constructive analysis**: algebraic cycles with non-Core nodes are errors (`CAUS001/002`); Core cycles must use monotone policies or `Delay`, otherwise `CAUS004`. Non-constructive SCCs raise `CAUS003`. `@contract(no_instant_loop=True)` or Delay edges exclude instant feedback from the graph.
- **Initialization (strict)**: every `Variable` and auto-generated Delay buffer must have an init/default (`INIT001/002`).
- **Non-Zeno**: reactions that read and write the same signal without delay must declare `@reaction(rank=..., max_microsteps=...)`, else `ZEN001`. Runtime guard raises `ZenoRuntimeError` if an SCC exceeds its microstep budget.
- **SDF rates**: inconsistent rates are `SDF001` errors; multi-rate graphs on a single clock surface as warnings (`passes_sdf.py`).
- **Continuous nodes**: `ContinuousWrapper` requires positive `dt` defaults and surfaces missing `state/y` outputs (`CT001/002/003`).

## Runtime semantics (discrete core)

- Tick = propose -> resolve -> commit over `Variable` intents; policies merge writes deterministically. Ports/vars use `ABSENT` as the "no value this tick" sentinel.
- SCCs run to a fixed point with microsteps (default limit 20, tightened by `max_microsteps` on reactions). Delay outputs are prefilled with last committed values each tick.
- `Delay(expr, default)` lowers to an explicit hidden `State` with init=`default`; reads happen at the start of the tick, writes are applied in commit.
- Ports are cleared each tick; `dt` inputs are auto-seeded when provided to `Pipeline.run(..., dt=...)`.

## Pipeline modes

- `best_effort` / `pragmatic`: full pass pipeline, but LWW multiwriters are warnings and init checks are skipped.
- `strict`: adds `InitPass` and upgrades LWW multiwriter conflicts to errors. Use this to enforce single-writer discipline and explicit inits.

## Quick start (discrete)

```python
from regelum.api import Pipeline
from regelum.core.core_node import CoreNode, Input, Output, State, reaction
from regelum.core.dsl import Expr, Delay

class Integrator(CoreNode):
    u = Input[float](default=0.0)
    y = Output[float]()
    acc = State[float](init=0.0)

    @reaction(rank="acc", max_microsteps=8)
    def step(self, u: Expr[float], acc: Expr[float]) -> Expr[float]:
        next_acc = acc + Delay(u, 0.0)
        self.acc.set(next_acc)
        return next_acc

pipe = Pipeline(mode="strict")
src = Integrator("src")
snk = Integrator("snk")
pipe.add(src, snk)
src.o.y >> snk.i.u
pipe.run(ticks=5, dt=0.01)
```

### Manual wiring

```python
src.o.y >> snk.i.u
```

Or, if port names match exactly across nodes, call `pipe.auto_wire(strict=True)` instead of manual connections.


## Authoring nodes

- **CoreNode**: reactions return DSL `Expr`; Python `if/while/len` over `Expr` is rejected at build time. Use `State.set(...)` for writes, `Delay` for hidden state, and annotate ranks when reading & writing the same signal. Ports are declared via `Input/Output`, optionally with `rate` and `default`.
- **ExtNode/RawNode**: free-form Python. Add contracts with `@contract(...)` or mark as `@unsafe("reason")`. They participate in structural checks but causality guarantees are limited; instant loops require `no_instant_loop=True`.
- **ContinuousNode**: define `ContinuousState` fields and `derivative/outputs`; wrap into discrete pipelines via `ContinuousWrapper` or `node.as_hybrid(...)` (ports: `u`, `state`, `y`, `dt`). Zero-order hold (`ZeroOrderHold`) and `Sampler` live in `core/hybrid_adapters.py`.

## Wiring helpers

- `>>` operator wires ports once nodes are added to a `Pipeline`/`GraphRuntime`.
- `Pipeline.auto_wire(strict=True)` connects matching port names; ambiguous matches error in strict mode, warn/skip otherwise.

## Examples

- `pipes/pendulum_pid.py` - Core control loop with feedback and Delay.
- `pipes/multirate_sdf.py` - SDF rates and schedule warnings.
- `pipes/router_fixed_point.py` - instant SCC with fixed-point resolution.
- `pipes/van_der_pol_hybrid.py` - hybrid continuous/discrete composition.

## Tooling

- Install uv (if needed): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Install deps: `uv sync`
- Run tests: `uv run pytest`
- Type check: `uv run pyright`
- Run examples: `uv run python pipes/pendulum_pid.py`
