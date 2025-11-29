import pytest
from regelum.api import Pipeline
from regelum.core.core_node import Input, Output, State, CoreNode, reaction
from regelum.core.ext_node import ExtNode
from regelum.core.dsl import if_, Var
from regelum.core.variables import ErrorPolicy
from dataclasses import dataclass

# --- Shared Data Structures ---
@dataclass
class AccountState:
    cash: float
    position: float
    equity: float

@dataclass
class OrderIntent:
    size: float
    reason: str

# --- Step 2: Multiple Writers ---

def test_audit_step2_multiple_writers():
    """
    Demonstrate Error S002: Multiple writers/Fan-in.
    """
    class AccountNode(ExtNode):
        state = State(init=AccountState(10000, 0, 10000), policy=ErrorPolicy())
        def step(self, ctx): pass

    class MarketSim(ExtNode):
        account_out = Output()
        def step(self, ctx): pass

    class RiskManager(ExtNode):
        account_out = Output()
        def step(self, ctx): pass

    pipe = Pipeline(mode="strict")
    acc = AccountNode("acc")
    market = MarketSim("market")
    risk = RiskManager("risk")
    
    pipe.add(acc, market, risk)
    
    # Manual fan-in connection
    # Pipeline.runtime is available
    acc.add_input("update", default=None)
    
    # Now that Runtime supports fan-in (list of edges), calling connect twice creates fan-in.
    pipe.runtime.connect(market.o.account_out, acc.i.update)
    pipe.runtime.connect(risk.o.account_out, acc.i.update)
    
    success = pipe.compile()
    
    assert not success
    # Expect STRUCT002 (Fan-in > 1)
    assert any(d.code == "STRUCT002" for d in pipe.report.diagnostics)

def test_audit_step3_algebraic_loop():
    """
    Demonstrate Error C003: Non-constructive instantaneous cycle.
    """
    class Strategy(CoreNode):
        price = Input(default=100.0)
        account = Input(default=AccountState(10000,0,10000))
        orders = Output()
        
        @reaction
        def propose(self, price: Var, account: Var):
            return if_(account > 100, 1.0, 0.0)

    class RiskManager(CoreNode):
        orders_in = Input(default=0.0)
        orders_out = Output()
        
        @reaction
        def enforce(self, orders_in: Var):
            return orders_in

    class MarketSim(CoreNode):
        orders = Input(default=0.0)
        account_out = Output()
        
        @reaction
        def apply(self, orders: Var):
            # Returns new account state (simplified to float for Expr connectivity)
            # If we return a dataclass, collect_vars misses the dependency unless we implement recursion.
            return orders + 1.0

    pipe = Pipeline(mode="strict")
    strat = Strategy("strat")
    risk = RiskManager("risk")
    market = MarketSim("market")
    
    pipe.add(strat, risk, market)
    
    # Wiring
    pipe.runtime.connect(strat.o.orders, risk.i.orders_in)
    pipe.runtime.connect(risk.o.orders_out, market.i.orders)
    pipe.runtime.connect(market.o.account_out, strat.i.account)
    
    success = pipe.compile()
    
    assert not success
    assert any(d.code == "CAUS003" for d in pipe.report.diagnostics)

def test_audit_step4_real_loop():
    class LoopNode(CoreNode):
        x_in = Input(default=0)
        x_out = Output()
        
        @reaction
        def step(self, x_in: Var):
            return x_in + 1

    pipe = Pipeline(mode="strict")
    node = LoopNode("loop")
    pipe.add(node)
    
    # Self loop
    pipe.runtime.connect(node.o.x_out, node.i.x_in)
    
    success = pipe.compile()
    
    assert not success
    assert any(d.code == "CAUS003" for d in pipe.report.diagnostics)

def test_audit_step5_init_check():
    class Reader(CoreNode):
        val = Input() # No default!
        out = Output()
        @reaction
        def step(self, val: Var):
            return val

    pipe = Pipeline(mode="strict")
    r = Reader("r")
    pipe.add(r)
    
    success = pipe.compile()
    
    assert not success
    # STRUCT001: Unconnected input without default
    assert any(d.code == "STRUCT001" for d in pipe.report.diagnostics)

def test_audit_step6_sdf():
    class Producer(ExtNode):
        out = Output(rate=1)
        def step(self, ctx): pass

    class Consumer(ExtNode):
        inp = Input(rate=32)
        def step(self, ctx): pass

    pipe = Pipeline(mode="strict")
    p = Producer("p")
    c = Consumer("c")
    pipe.add(p, c)
    
    pipe.runtime.connect(p.o.out, c.i.inp)
    
    success = pipe.compile()
    
    # SDF warning should allow success but appear in diagnostics
    assert success
    assert any(d.code == "SDF001" for d in pipe.report.diagnostics)
