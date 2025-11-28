import pytest
from rg_compiler.core.node import RawNode, Context, IntentContext
from rg_compiler.core.runtime import GraphRuntime
from rg_compiler.core.variables import Variable, SumPolicy, ErrorPolicy, LWWPolicy

class VarWriterNode(RawNode):
    def __init__(self, node_id: str, var: Variable[int], val: int):
        super().__init__(node_id)
        self.var = var
        self.val = val

    def step(self, ctx: Context) -> None:
        assert isinstance(ctx, IntentContext)
        ctx.write_var(self.var, self.val)

class VarReaderNode(RawNode):
    def __init__(self, node_id: str, var: Variable[int]):
        super().__init__(node_id)
        self.var = var
        self.last_read: int | None = None

    def step(self, ctx: Context) -> None:
        assert isinstance(ctx, IntentContext)
        self.last_read = ctx.read_var(self.var)

def test_sum_policy():
    var = Variable("cash", 0, SumPolicy())
    
    runtime = GraphRuntime()
    # Two writers
    w1 = VarWriterNode("W1", var, 10)
    w2 = VarWriterNode("W2", var, 20)
    # Reader
    r = VarReaderNode("R", var)
    
    runtime.add_node(w1)
    runtime.add_node(w2)
    runtime.add_node(r)
    
    # No edges, so schedule order is arbitrary (or insertion order)
    runtime.build_schedule()
    
    # Tick 1
    # R reads init (0)
    # W1 writes 10
    # W2 writes 20
    # Commit -> cash = 30
    runtime.run_tick()
    
    assert r.last_read == 0
    assert runtime.var_state["cash"] == 30
    
    # Tick 2
    # R reads 30
    runtime.run_tick()
    assert r.last_read == 30
    
    # Writers write again -> +30
    # Commit -> cash = 30? No, SumPolicy merges current intents.
    # It does NOT add to previous value automatically unless logic does read+write.
    # My logic: ctx.write_var(val).
    # Resolve: sum([10, 20]) = 30.
    # Commit: var_state["cash"] = 30.
    # So it resets if we don't read+add.
    assert runtime.var_state["cash"] == 30

def test_error_policy():
    var = Variable("single", 0, ErrorPolicy())
    runtime = GraphRuntime()
    w1 = VarWriterNode("W1", var, 1)
    w2 = VarWriterNode("W2", var, 2)
    
    runtime.add_node(w1)
    runtime.add_node(w2)
    runtime.build_schedule()
    
    with pytest.raises(ValueError, match="Multiple writes detected"):
        runtime.run_tick()

def test_lww_policy():
    # Priority: W2 > W1
    var = Variable("last", 0, LWWPolicy(priority_order=["W1", "W2"]))
    runtime = GraphRuntime()
    w1 = VarWriterNode("W1", var, 100)
    w2 = VarWriterNode("W2", var, 200)
    
    runtime.add_node(w1)
    runtime.add_node(w2)
    runtime.build_schedule()
    
    runtime.run_tick()
    # W2 wins
    assert runtime.var_state["last"] == 200
    
    # Try reverse priority
    var2 = Variable("last2", 0, LWWPolicy(priority_order=["W2", "W1"]))
    runtime2 = GraphRuntime()
    w1b = VarWriterNode("W1", var2, 100)
    w2b = VarWriterNode("W2", var2, 200)
    runtime2.add_node(w1b)
    runtime2.add_node(w2b)
    runtime2.build_schedule()
    
    runtime2.run_tick()
    # W1 wins
    assert runtime2.var_state["last2"] == 100

