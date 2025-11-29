from __future__ import annotations

import torch
from torch import nn

from rg_compiler.api import Pipeline
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.node import Context
from rg_compiler.core.contracts import contract
from rg_compiler.core.core_node import Input, Output, reaction
from rg_compiler.core.dsl import Expr, BinOp


class PolicyModule(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, 64),
            nn.Tanh(),
            nn.Linear(64, act_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class PolicyNode(ExtNode):
    obs = Input[torch.Tensor]()
    action = Output[torch.Tensor]()

    def __init__(self, node_id: str, module: nn.Module):
        super().__init__(node_id)
        self.module = module

    @contract(deterministic=True, no_side_effects=True, no_instant_loop=True)
    def step(self, ctx: Context):
        obs = ctx.read(self.inputs["obs"])
        if obs is None:
            return
        with torch.no_grad():
            act = self.module(obs)
        ctx.write(self.outputs["action"], act)


class RewardNode(ExtNode):
    obs = Input[torch.Tensor]()
    action = Input[torch.Tensor]()
    reward = Output[torch.Tensor]()

    def __init__(self, node_id: str):
        super().__init__(node_id)

    @contract(deterministic=True, no_side_effects=True, no_instant_loop=True)
    def step(self, ctx: Context):
        obs = ctx.read(self.inputs["obs"])
        act = ctx.read(self.inputs["action"])
        if obs is None or act is None:
            return
        # toy reward: negative L2 norm of action
        rew = -(act.pow(2).sum(dim=-1, keepdim=True))
        ctx.write(self.outputs["reward"], rew)


class AdvantageNode(ExtNode):
    reward = Input[torch.Tensor]()
    baseline = Input[torch.Tensor](default=torch.tensor(0.0))
    adv = Output[torch.Tensor]()

    def __init__(self, node_id: str):
        super().__init__(node_id)

    @contract(deterministic=True, no_side_effects=True, no_instant_loop=True)
    def step(self, ctx: Context):
        r = ctx.read(self.inputs["reward"])
        b = ctx.read(self.inputs["baseline"])
        if r is None:
            return
        ctx.write(self.outputs["adv"], r - b)


class BaselineNode(ExtNode):
    reward = Input[torch.Tensor]()
    baseline = Output[torch.Tensor]()

    def __init__(self, node_id: str, momentum: float = 0.9):
        super().__init__(node_id)
        self._ema = None
        self._momentum = momentum

    @contract(deterministic=True, no_side_effects=False, no_instant_loop=True)
    def step(self, ctx: Context):
        r = ctx.read(self.inputs["reward"])
        if r is None:
            return
        mean = r.mean()
        if self._ema is None:
            self._ema = mean
        else:
            self._ema = self._momentum * self._ema + (1 - self._momentum) * mean
        ctx.write(self.outputs["baseline"], self._ema)


def build_policy_pipeline(obs_dim: int, act_dim: int) -> Pipeline:
    pipe = Pipeline(mode="strict")
    policy = PolicyNode("pi", PolicyModule(obs_dim, act_dim))
    reward = RewardNode("reward")
    baseline = BaselineNode("baseline")
    adv = AdvantageNode("adv")

    pipe.add(policy, reward, baseline, adv)
    pipe.auto_wire(strict=False)
    return pipe


def run_demo():
    pipe = build_policy_pipeline(obs_dim=4, act_dim=2)
    import torch

    obs = torch.randn(1, 4)
    pipe.run(ticks=1, inputs={pipe.runtime.nodes["pi"].inputs["obs"]: obs})
    act = pipe.runtime.port_state[pipe.runtime.nodes["pi"].outputs["action"]]
    print("action", act)


if __name__ == "__main__":
    run_demo()
