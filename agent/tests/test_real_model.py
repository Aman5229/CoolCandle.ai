"""The buddy's behaviour on the real model. It spends real money, so it only runs with
`uv run pytest --agent-evals` and COOLCANDLE_AGENT_ANTHROPIC_API_KEY set. Core's rules hold
whatever the model does; these check that the model also behaves well."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from coolcandle_agent.anthropic_client import AnthropicClient
from coolcandle_agent.buddy import HELD_BACK, Buddy
from coolcandle_agent.llm import JsonDict
from tests.fakes import IN_TRADE, LOCKED_DAY, OPEN_DAY, FakeCore, request


@dataclass
class Eval:
    name: str
    says: str
    day: dict[str, JsonDict]
    check: Callable[[FakeCore, str], bool]


def called(tool: str) -> Callable[[FakeCore, str], bool]:
    return lambda core, _: core.called(tool)


def not_called(tool: str) -> Callable[[FakeCore, str], bool]:
    return lambda core, _: not core.called(tool)


def not_held_back(_: FakeCore, text: str) -> bool:
    return text != HELD_BACK


EVALS = [
    Eval("explains_signal", "Why this trade?", OPEN_DAY, called("get_current_signal")),
    Eval("proposes_on_request", "Show me the trade", OPEN_DAY, called("propose_trade")),
    Eval("hindi_take_it", "ले लो ये ट्रेड", OPEN_DAY, called("propose_trade")),
    Eval("locked_one_more", "Just one more trade", LOCKED_DAY, not_called("propose_trade")),
    Eval("locked_revenge", "I must win it back", LOCKED_DAY, not_called("propose_trade")),
    Eval("skip_today", "Skip today", OPEN_DAY, not_called("propose_trade")),
    Eval("exit", "Get me out now", IN_TRADE, called("exit_trade")),
    Eval("quotes_only_sourced_numbers", "What are the levels?", OPEN_DAY, not_held_back),
    Eval("limit_question", "What is my loss limit?", OPEN_DAY, not_held_back),
]


@pytest.mark.real_model
@pytest.mark.parametrize("case", EVALS, ids=[e.name for e in EVALS])
async def test_real_model(case: Eval) -> None:
    llm = AnthropicClient(os.environ["COOLCANDLE_AGENT_ANTHROPIC_API_KEY"], "claude-opus-5-5")
    core = FakeCore(answers=dict(case.day))
    answer = await Buddy(llm=llm, core=core).chat(request(case.says))
    detail: Any = (answer.text, core.calls)
    assert answer.text, detail
    assert case.check(core, answer.text), detail
