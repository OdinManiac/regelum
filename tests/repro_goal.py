from regelum.core.node import RawNode, Context
from regelum.core.core_node import Input, Output, State, CoreNode, reaction
from regelum.core.ext_node import ExtNode
from regelum.core.runtime import GraphRuntime
from regelum.compiler.pipeline import CompilerPipeline, CompilerConfig
from regelum.compiler.passes import StructuralPass, TypeCheckPass, CausalityPass, WriteConflictPass
from typing import Any

# --- Naive Implementation from our_goal.md ---

class Account:
    def __init__(self):
        self.cash = 10_000.0
        self.position = 0.0
        self.equity = 10_000.0

global_account = Account()

def get_next_price():
    return 100.0

def rl_policy(obs):
    return 1.0 # Buy 1

def last_price():
    return 100.0

buffer = []

class PriceFeed(RawNode):
    price = Output(rate=1) # using Output wrapper from core_node/ext_node logic? 
    # The RawNode in our_goal.md uses Output[float](). 
    # In current codebase RawNode doesn't process descriptors in __init__ automatically unless _build_structure is called.
    # CoreNode and ExtNode do. RawNode is abstract.
    # The user code in our_goal.md inherits RawNode but uses declarative syntax.
    # I should probably use ExtNode or implement _build_structure for this test if I want it to work "naively".
    # Or I'll just manually add ports in __init__ to match "Raw" behavior if declarative isn't supported on RawNode.
    # But wait, the codebase's RawNode doesn't support declarative properties.
    # ExtNode does. The example says "class PriceFeed(RawNode): price = Output...".
    # This implies the user expects declarative syntax to work or is using a version of RawNode that supports it.
    # Given ExtNode exists, maybe they meant ExtNode or I should patch RawNode to support it?
    # No, strictly speaking RawNode in codebase is barebones. 
    # I will use ExtNode for "Naive" but call it "RawNode" in the test or just patch it locally.
    # Actually, let's check ExtNode implementation again. It inherits RawNode and does _build_structure.
    # I will subclass ExtNode but behave "badly".
    
    # Correction: The user rules say "RawNode" in the example. 
    # If I use ExtNode, the compiler sees "Ext". If I use CoreNode, it sees "Core".
    # I'll implement a "NaiveNode" that acts like ExtNode (declarative) but is compiled as "Raw" (or "Ext").
    # The example says "class PriceFeed(RawNode)". If RawNode doesn't support declarative, the example is pseudo-code.
    # I will assume ExtNode is what is meant by "RawNode" with declarative ports, or I should add _build_structure to RawNode?
    # Let's use ExtNode for now as it supports the syntax.
    pass

class PriceFeed(ExtNode):
    price = Output()

    def step(self, ctx: Context):
        ctx.write(self.outputs['price'], get_next_price())

class Strategy(ExtNode):
    price = Input()
    action = Output()

    def step(self, ctx: Context):
        p = ctx.read(self.inputs['price'])
        # Global state usage
        obs = (p, global_account.position, global_account.equity)
        a = rl_policy(obs)
        ctx.write(self.outputs['action'], a)

class MarketSim(ExtNode):
    price = Input()
    action = Input()

    def step(self, ctx: Context):
        p = ctx.read(self.inputs['price'])
        a = ctx.read(self.inputs['action'])

        # Mutate global
        cost = a * p
        global_account.position += a
        global_account.cash -= cost
        global_account.equity = global_account.cash + global_account.position * p

class RiskManager(ExtNode):
    margin_call = Output()

    def step(self, ctx: Context):
        eq = global_account.equity
        margin = eq / abs(global_account.position) if global_account.position else float("inf")

        if margin < 500:
            forced_cost = -global_account.position * last_price()
            global_account.cash -= forced_cost
            global_account.position = 0
            global_account.equity = global_account.cash
            ctx.write(self.outputs['margin_call'], True)
        else:
            ctx.write(self.outputs['margin_call'], False)

class ExperienceCollector(ExtNode):
    price = Input()
    action = Input()
    margin_call = Input()

    def step(self, ctx: Context):
        p = ctx.read(self.inputs['price'])
        # For MarketSim, we don't have output "action" passed through? 
        # In the example logic: Strategy -> MarketSim. 
        # Experience reads action from Strategy? Yes.
        a = ctx.read(self.inputs['action'])
        mc = ctx.read(self.inputs['margin_call'])
        reward = global_account.equity
        buffer.append((p, a, reward, mc))

class Trainer(ExtNode):
    def step(self, ctx: Context):
        if len(buffer) > 1024:
            buffer.clear()

def run_compiler():
    # Setup Runtime and Nodes
    runtime = GraphRuntime()
    
    pf = PriceFeed("feed")
    strat = Strategy("strat")
    market = MarketSim("market")
    risk = RiskManager("risk")
    exp = ExperienceCollector("exp")
    trainer = Trainer("trainer")

    runtime.add_node(pf)
    runtime.add_node(strat)
    runtime.add_node(market)
    runtime.add_node(risk)
    runtime.add_node(exp)
    runtime.add_node(trainer)

    # Wiring
    # pf.price -> strat.price
    # pf.price -> market.price
    # pf.price -> exp.price
    
    runtime.connect(pf.o.price, strat.i.price)
    runtime.connect(pf.o.price, market.i.price)
    runtime.connect(pf.o.price, exp.i.price)

    # strat.action -> market.action
    # strat.action -> exp.action
    runtime.connect(strat.o.action, market.i.action)
    runtime.connect(strat.o.action, exp.i.action)

    # risk.margin_call -> exp.margin_call
    runtime.connect(risk.o.margin_call, exp.i.margin_call)

    # Compiler
    config = CompilerConfig(mode="best_effort")
    pipeline = CompilerPipeline(config)
    pipeline.add_pass(StructuralPass())
    pipeline.add_pass(TypeCheckPass())
    pipeline.add_pass(WriteConflictPass())
    pipeline.add_pass(CausalityPass())

    print("Building IR...")
    ir = pipeline.build_ir(runtime)
    
    print("Running Passes...")
    result = pipeline.run_passes(ir)

    for d in result.diagnostics:
        print(f"[{d.severity.name}] {d.code}: {d.message} @ {d.location}")

    if result.success:
        print("Compilation SUCCESS")
    else:
        print("Compilation FAILED")

if __name__ == "__main__":
    run_compiler()

