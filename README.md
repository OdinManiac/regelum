# RG Compiler

A framework for designing complex reactive pipelines with formal guarantees.

## Status

Implemented Stages 0-12 (Core + Analysis + UX):

- **Stage 0-8**: Core runtime, DSL, and Formal Analysis (Causality, SDF).
- **Stage 9**: Contracts for external code (`@contract`, `@unsafe`).
- **Stage 10**: Wiring DSL (`>>`).
- **Stage 11**: Pipeline Facade & Input Defaults.
- **Stage 12**: Auto-wiring by name.

## Usage

### Quick Start with Pipeline Facade

```python
from rg_compiler.api import Pipeline
from rg_compiler.core.core_node import CoreNode, Input, Output, reaction
from rg_compiler.core.dsl import Expr

class Source(CoreNode):
    data = Output[int]()
    @reaction
    def produce(self) -> Expr[int]: return 10

class Sink(CoreNode):
    data = Input[int]()
    @reaction
    def consume(self, data: Expr[int]): pass

# 1. Create Pipeline
pipe = Pipeline()

# 2. Add Nodes
src = Source("src")
snk = Sink("sink")
pipe.add(src, snk)

# 3. Auto-wire (matches 'data' output to 'data' input)
pipe.auto_wire(strict=True)

# 4. Run (compiles automatically)
pipe.run(ticks=5)
```

### Manual Wiring

```python
# Using >> operator
src.o.data >> snk.i.data
```

### Defining Nodes

See `pipes/pendulum_pid.py` for advanced examples including State, Feedback loops, and ExtNodes.
