"""The conversation loop: tool calls go to core with the chat's token, numbers are checked, and
only exchanges the trader saw stay in the history."""

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest

from coolcandle_agent.buddy import HELD_BACK, MAX_STEPS, NOT_SET_UP, REFUSED, TOO_LONG, Buddy
from coolcandle_agent.llm import JsonDict, LlmTurn, ToolSpec
from tests.fakes import DAY, TOOL_TOKEN, FakeCore, ScriptedLlm, request

SIGNAL: tuple[str, JsonDict] = ("get_current_signal", {})
PLAN: tuple[str, JsonDict] = ("get_plan", {})


def buddy(llm: Any, core: FakeCore | None = None) -> Buddy:
    return Buddy(llm=llm, core=core or FakeCore())


async def test_without_a_key_it_says_so() -> None:
    answer = await Buddy(llm=None, core=FakeCore()).chat(request("hi"))
    assert answer.text == NOT_SET_UP


async def test_tools_go_to_core_with_this_chats_token() -> None:
    core = FakeCore()
    llm = ScriptedLlm([SIGNAL, ("propose_trade", {"signal_id": "s1"})], "Tap Confirm to take it.")
    answer = await buddy(llm, core).chat(request("Show me the trade"))
    assert answer.text == "Tap Confirm to take it."
    assert core.calls == [
        (TOOL_TOKEN, "get_current_signal", {}),
        (TOOL_TOKEN, "propose_trade", {"signal_id": "s1"}),
    ]
    results = llm.seen[-1][-1]["results"]
    assert [failed for _, _, failed in results] == [False]
    assert json.loads(results[0][1])["note"] == "Nothing is placed."


@pytest.mark.parametrize(
    ("calls", "reply"),
    [
        ([], "Enter at 87.45 now."),
        ([SIGNAL], "Use a stop of 61.5."),
        ([], "Target is 215."),
        ([], "You'll make 4,500 today."),
        ([], "Buy 12 lots."),
        ([PLAN], "Your profit target is 5000 and that means 6000 is fine."),
    ],
    ids=["entry", "stop", "target", "pnl", "lots", "one_bad_among_good"],
)
async def test_an_invented_number_is_held_back(calls: list[Any], reply: str) -> None:
    answer = await buddy(ScriptedLlm(calls, reply)).chat(request("Levels?"))
    assert answer.text == HELD_BACK


@pytest.mark.parametrize(
    ("calls", "reply"),
    [
        ([SIGNAL], "Entry 100, stop 90, target 120, valid till 10:42."),
        ([PLAN], "Your loss limit is 3,000."),
        ([], "Sure, skipping today."),
    ],
    ids=["signal", "plan_with_grouping", "no_numbers"],
)
async def test_sourced_numbers_pass(calls: list[Any], reply: str) -> None:
    answer = await buddy(ScriptedLlm(calls, reply)).chat(request("Levels?"))
    assert answer.text == reply


async def test_the_traders_own_numbers_can_be_repeated() -> None:
    answer = await buddy(ScriptedLlm([], "You asked about 24500.")).chat(
        request("What about 24500?")
    )
    assert answer.text == "You asked about 24500."


async def test_a_number_in_a_failed_tool_is_not_a_source() -> None:
    core = FakeCore(failing={"exit_trade"})
    llm = ScriptedLlm([("exit_trade", {})], "It failed at 77.25.")
    answer = await buddy(llm, core).chat(request("Get me out"))
    assert answer.text == HELD_BACK
    assert llm.seen[-1][-1]["results"][0][2] is True


async def test_a_conversation_carries_on_through_the_day() -> None:
    llm = ScriptedLlm([], "First.")
    chat = buddy(llm)
    await chat.chat(request("one"))
    llm.reply = "Second."
    await chat.chat(request("two"))
    assert [m.get("content") for m in llm.seen[-1] if m["role"] == "user"] == ["one", "two"]


async def test_a_new_trading_day_starts_fresh() -> None:
    llm = ScriptedLlm([], "Okay.")
    chat = buddy(llm)
    await chat.chat(request("yesterday"))
    await chat.chat(request("today", day=DAY + timedelta(days=1)))
    assert llm.seen[-1] == [{"role": "user", "content": "today"}]


async def test_traders_do_not_share_a_conversation() -> None:
    llm = ScriptedLlm([], "Okay.")
    chat = buddy(llm)
    await chat.chat(request("mine", conversation="u1"))
    await chat.chat(request("theirs", conversation="u2"))
    assert llm.seen[-1] == [{"role": "user", "content": "theirs"}]


async def _history_after(first: Any) -> list[Any]:
    """Run `first` as the opening exchange, then a plain one, and return what the model saw."""
    chat = buddy(first)
    try:
        await chat.chat(request("first"))
    except RuntimeError:
        pass
    after = ScriptedLlm([], "Okay.")
    chat.llm = after
    await chat.chat(request("second"))
    return after.seen[-1]


async def test_a_held_back_reply_is_forgotten() -> None:
    assert await _history_after(ScriptedLlm([], "Buy 12 lots.")) == [
        {"role": "user", "content": "second"}
    ]


async def test_a_refusal_is_handled_and_forgotten() -> None:
    class Refuses(ScriptedLlm):
        async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn:
            return LlmTurn(text="", refused=True)

    assert (await buddy(Refuses()).chat(request("hello"))).text == REFUSED
    assert await _history_after(Refuses()) == [{"role": "user", "content": "second"}]


async def test_a_runaway_tool_loop_stops_and_is_forgotten() -> None:
    llm = ScriptedLlm([PLAN] * 20, "never")
    core = FakeCore()
    assert (await buddy(llm, core).chat(request("loop"))).text == TOO_LONG
    assert len(core.calls) == MAX_STEPS
    assert await _history_after(ScriptedLlm([PLAN] * 20, "never")) == [
        {"role": "user", "content": "second"}
    ]


async def test_a_failure_halfway_leaves_no_dangling_tool_call() -> None:
    class DiesAfterATool(ScriptedLlm):
        async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn:
            if len(messages) > 1:
                raise RuntimeError("model API down")
            return await super().turn(system, messages, tools)

    assert await _history_after(DiesAfterATool([PLAN])) == [{"role": "user", "content": "second"}]


async def test_two_messages_at_once_take_turns() -> None:
    class Slow(ScriptedLlm):
        active = 0
        overlapped = False

        async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn:
            Slow.active += 1
            Slow.overlapped = Slow.overlapped or Slow.active > 1
            await asyncio.sleep(0.01)
            Slow.active -= 1
            return await super().turn(system, messages, tools)

    chat = buddy(Slow())
    await asyncio.gather(chat.chat(request("a")), chat.chat(request("b")))
    assert not Slow.overlapped
