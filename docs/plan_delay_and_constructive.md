# Delay + Constructive Semantics Blueprint

## Goals

1. Give `Delay(expr, default)` the semantics described in `дискуссия.md`: emit previous-tick value, schedule update with current expression, participate in intents/write policies like any other state.
2. Align constructive/causality analysis with real runtime data by operating on actual defaults, `ABSENT` semantics, and declared lattice modes.

## Surfaces & Touch Points

| Area | Actions |
|------|---------|
| DSL (`core/dsl.py`) | Keep `Delay` AST node, but annotate with a unique synthetic state id so compilation can materialize storage automatically. |
| CoreNode compilation (`core/core_node.py`) | When a reaction AST contains `Delay`, allocate a hidden `State` (`_delay__{reaction}__{idx}`) with `init=delay.default`, `policy=ErrorPolicy`. Re-write the AST: replace `Delay(expr, default)` with a `Var` referencing the hidden state; record “post-step write” for that state as `expr`. |
| Interpreter (`core/interpreter.py`) | Remove “return default” hack. During simulation, env already contains the hidden state value; evaluation should just read variables. |
| Runtime intent handling (`core/runtime.py`) | Ensure propose phase seeds env with current hidden-state values and that commit phase applies intents for them (they are regular `Variable`s once materialized). |
| IR generation (`compiler/pipeline.py`) | Hidden states appear like normal `IRVariable`s; no special casing beyond tagging as `autogen` for diagnostics. |
| Non-Zeno annotations | `@reaction(rank=\"counter\", max_microsteps=...)` metadata travels through IR so `NonZenoPass` + runtime guard can enforce microstep termination. |
| Tests | Extend `tests/test_stage3_dsl.py`, `test_stage5_constructive.py`, and `test_stage7_zeno.py` to cover delay semantics, constructive failures, and non-Zeno enforcement. |

## Data Flow (per reaction)

1. **Author’s AST**: `Delay(f(x), default)`.
2. **During `_compile_single_reaction`**:
   - Traverse AST, whenever `Delay` encountered:
     - Generate hidden state name `__delay_{reaction}_{counter}`.
     - Register `State(init=default)` if not present.
     - Replace node with `Var(hidden_state_local_name)`.
     - Record post-step write: hidden state <- original `expr`.
3. **At runtime**:
   - Reaction reads hidden state (previous tick) via normal `env`.
   - After evaluating reaction, IntentContext writes the queued expression value into the hidden state.

This makes `Delay` a zero-delay read + scheduled write, aligning with the “explicit state, no magic” philosophy.

## Constructive Analysis Enhancements

1. **Seed Environment**:
   - For each state: `V3.present(init)` if init exists, else error in strict mode.
   - For each input: `present(default)` if default provided, else `absent`.
2. **Value Representation**:
   - `V3` becomes tagged (`present`, `absent`, `bottom`) with optional payload.
   - Boolean guards operate in Kleene three-valued logic; data ops propagate `bottom` when operands unknown.
3. **Lattice Metadata**:
   - Extend `State` to accept `mode` descriptor (e.g., `DeltaSum`, `LastWriterWins(priority=...)`, `ErrorPolicy`).
   - Provide `mode.height_bound()` and `mode.is_monotone()` helpers for SCC validation.
4. **Iteration Strategy**:
   - Iterate until fixpoint or `max_iter` derived from sum of height bounds; if exhausted, raise CAUS003 with hint (e.g., “declare monotone mode” or “insert delay”).
5. **External Dependencies**:
   - When a reaction reads a value outside the SCC, inject the known `V3` for that variable/port; *never* default to literal zero.

## Diagnostics & UX

| Scenario | Message |
|----------|---------|
| Hidden state missing init | `INIT002`: “Delay-backed state '__delay...' lacks init; provide default in Delay(...)”. |
| Cycle with non-monotone mode | `CAUS004`: “Variable 'foo' in SCC uses non-monotone policy 'LWW'; declare monotone mode or insert delay.” |
| Fixed-point iteration exhausted | `CAUS005`: include SCC member list, suggested fixes (delay, ranking, monotone mode). |
| Missing rank | `ZEN001`: “Instantaneous cycle [...] lacks a non-zeno rank. Annotate @reaction(rank=..., max_microsteps=...).” |

## Non-Zeno Guarantees

1. **Author intent**: `@reaction(rank="remaining", max_microsteps=32)` declares a well-founded ranking expression (usually a state or counter) and an optional per-SCC microstep limit.
2. **Compile time**: `NonZenoPass` walks SCCs in the dependency graph; any instantaneous SCC lacking at least one ranked reaction fails with `ZEN001`.
3. **Runtime guard**: `_run_scc_loop` consults the tightest `max_microsteps` among ranked reactions (default 20) and raises `ZenoRuntimeError` if convergence never happens, surfacing the SCC members for debugging.
4. **Regression coverage**: `tests/test_stage7_zeno.py` now asserts both the compile-time failure (missing rank) and the runtime guard behavior using a synthetic oscillator node.

## Next Steps

1. Harden diagnosis UX (`CAUS005` vs `ZEN001`) with SCC excerpts and fix-hints that point to offending reactions and ranks.
2. Extend documentation/STAGES with concrete walkthroughs (before/after) so users know when to declare `rank` vs when to insert `Delay`.
3. Stress-test mixed-mode graphs (Core + Ext) so NonZeno and constructive passes stay aligned with runtime guard behavior.

