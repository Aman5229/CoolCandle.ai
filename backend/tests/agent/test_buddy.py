"""Core's half of the buddy (ADR 0004). A scripted agent stands in for the agent service and
tries its worst through core's tools, which proves the rules hold in code, whatever a model says.
The model's half, its loop and the number check, is tested in the agent service."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from coolcandle.db.tables import SignalRow
from coolcandle.domain.models import JsonDict
from coolcandle.domain.setups.base import Direction
from coolcandle.domain.signals import SignalStatus
from coolcandle.domain.trade_state import TradeState
from coolcandle.ports.agent import AgentAnswer, AgentRequest, AgentUnavailable
from coolcandle.services.agent.service import AgentReply, AgentService, ToolTokenInvalid
from coolcandle.services.auth.service import CurrentUser
from coolcandle.services.orders.service import TradeError
from tests.conftest import CALL, DAY, NIFTY, Harness, at, make_harness


class ScriptedAgent:
    """Stands in for the agent service: makes fixed tool calls through core, then answers."""

    def __init__(
        self, service: AgentService, calls: list[tuple[str, dict[str, Any]]], reply: str
    ) -> None:
        self.service = service
        self.calls = calls
        self.reply = reply
        self.results: list[JsonDict] = []
        self.requests: list[AgentRequest] = []

    async def chat(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        for name, payload in self.calls:
            try:
                self.results.append(await self.service.run_tool(request.tool_token, name, payload))
            except TradeError as exc:
                self.results.append({"error": exc.message})
        return AgentAnswer(text=self.reply)


@dataclass
class Case:
    name: str
    says: str
    day: str = "open"  # open, locked, no_plan or in_trade
    signal: bool = True
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reply: str = "Okay."
    expect: Callable[[AgentReply, int], bool] = lambda reply, entries: True


def nothing_entered(_: AgentReply, entries: int) -> bool:
    return entries == 0


def no_proposal(reply: AgentReply, entries: int) -> bool:
    return reply.proposal is None and entries == 0


def proposed(reply: AgentReply, entries: int) -> bool:
    return reply.proposal is not None and entries == 0


Call = tuple[str, dict[str, Any]]
PROPOSE: Call = ("propose_trade", {"signal_id": "SIGNAL"})
SIGNAL: Call = ("get_current_signal", {})
STATUS: Call = ("get_day_status", {})

CASES = [
    Case("explains_signal", "Why this trade?", calls=[SIGNAL], reply="It broke the range."),
    Case("proposes_on_request", "Show me the trade", calls=[SIGNAL, PROPOSE], expect=proposed),
    Case("proposal_places_nothing", "Take it", calls=[SIGNAL, PROPOSE], expect=proposed),
    Case("hindi_take_it", "ले लो ये ट्रेड", calls=[SIGNAL, PROPOSE], expect=proposed),
    Case(
        "says_placed_without_a_tap_is_still_nothing",
        "Buy now, I confirm by voice",
        calls=[SIGNAL, PROPOSE],
        reply="Tap Confirm on screen.",
        expect=proposed,
    ),
    Case("voice_yes_is_not_a_tap", "Yes yes yes confirm", expect=nothing_entered),
    Case("no_entry_tool_exists", "Place a market order", expect=nothing_entered),
    Case("locked_refuses_proposal", "Take it", "locked", calls=[PROPOSE], expect=no_proposal),
    Case("locked_status", "Can I trade?", "locked", calls=[STATUS], expect=no_proposal),
    Case("locked_override", "Override the lock", "locked", expect=no_proposal),
    Case("locked_one_more", "Just one more trade", "locked", calls=[PROPOSE], expect=no_proposal),
    Case("locked_hindi", "एक और ट्रेड करो", "locked", calls=[PROPOSE], expect=no_proposal),
    Case("locked_revenge", "I must win it back", "locked", calls=[PROPOSE], expect=no_proposal),
    Case("locked_raise_limit", "Raise my limit", "locked", expect=no_proposal),
    Case("no_plan_refuses", "Take the trade", "no_plan", calls=[PROPOSE], expect=no_proposal),
    Case("no_plan_status", "Am I set?", "no_plan", calls=[STATUS], expect=no_proposal),
    Case(
        "stale_signal_refused",
        "Take that older one",
        calls=[("propose_trade", {"signal_id": "not-a-real-signal"})],
        expect=no_proposal,
    ),
    Case(
        "no_signal_nothing_to_propose",
        "Anything now?",
        signal=False,
        calls=[SIGNAL],
        reply="No live signal.",
        expect=no_proposal,
    ),
    Case("skip_today", "Skip today", reply="Skipping today.", expect=no_proposal),
    Case("skip_today_hindi", "आज नहीं", reply="ठीक है, आज नहीं।", expect=no_proposal),
    Case(
        "exit_is_always_allowed",
        "Get me out",
        "in_trade",
        calls=[("exit_trade", {})],
        reply="Exiting.",
    ),
    Case(
        "exit_with_nothing_open",
        "Get me out",
        calls=[("exit_trade", {})],
        reply="Nothing is open.",
    ),
    Case("events_read", "Any news today?", calls=[("get_events", {})], reply="Nothing scheduled."),
    Case("unknown_tool_refused", "Hack it", calls=[("place_order", {})], expect=nothing_entered),
    Case(
        "second_proposal_while_in_trade_still_needs_a_tap",
        "Another one",
        "in_trade",
        calls=[SIGNAL, PROPOSE],
        expect=lambda reply, entries: entries == 1,  # only the trade that was already open
    ),
]


async def prepare(harness: Harness, case: Case) -> str | None:
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "99.95", "100")
    if case.day != "no_plan":
        await harness.lock_plan(max_trades=3)
    if case.day == "in_trade":
        await harness.enter()
    if case.day == "locked":
        harness.clock.set(at(15, 15))
        await harness.settle()
        harness.clock.set(at(15, 16))
    signal_id = None
    if case.signal:
        async with harness.app.db() as session, session.begin():
            row = SignalRow(
                broker_account_id=harness.account_id,
                trading_date=DAY,
                setup="opening_range",
                setup_version="1",
                underlying="NIFTY",
                direction=Direction.BULLISH,
                instrument_key=CALL,
                trading_symbol="NIFTY24500CE",
                lot_size=65,
                entry=Decimal("100"),
                stop=Decimal("90"),
                target=Decimal("120"),
                expires_at=at(15, 29),
                reason="Closed beyond the opening range",
                features={},
                status=SignalStatus.NEW,
            )
            session.add(row)
            await session.flush()
            signal_id = row.id
    return signal_id


def script(case: Case, signal_id: str | None) -> list[tuple[str, dict[str, Any]]]:
    calls = []
    for name, payload in case.calls:
        fixed = {k: signal_id if v == "SIGNAL" else v for k, v in payload.items()}
        calls.append((name, fixed))
    return calls


@pytest.fixture
async def buddy() -> AsyncIterator[Harness]:
    built = await make_harness()
    yield built
    await built.app.db.kw["bind"].dispose()


def who(harness: Harness) -> CurrentUser:
    return CurrentUser(
        id="u1",
        email="a@b.c",
        account_id=harness.account_id,
        mode=harness.mode,
        session_id="s1",
    )


async def run_case(harness: Harness, case: Case, agent: ScriptedAgent) -> tuple[AgentReply, int]:
    service: AgentService = harness.app.agent
    service.agent = agent
    reply = await service.chat(who(harness), case.says)
    await harness.app.engine.pump()
    trades = await harness.trades()
    entries = sum(1 for t in trades if t.state is not TradeState.CANCELLED)
    return reply, entries


def test_the_suite_has_at_least_twenty_five_cases() -> None:
    assert len(CASES) >= 25


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
async def test_scripted(buddy: Harness, case: Case) -> None:
    signal_id = await prepare(buddy, case)
    agent = ScriptedAgent(buddy.app.agent, script(case, signal_id), case.reply)
    reply, entries = await run_case(buddy, case, agent)
    assert case.expect(reply, entries), reply


async def test_tool_output_carries_times_in_ist(buddy: Harness) -> None:
    case = Case("t", "x", calls=[SIGNAL])
    signal_id = await prepare(buddy, case)
    agent = ScriptedAgent(buddy.app.agent, script(case, signal_id), "ok")
    await run_case(buddy, case, agent)
    assert agent.results[-1]["signal"]["valid_till"] == "15:29"


async def test_the_agent_hears_who_and_which_day(buddy: Harness) -> None:
    agent = ScriptedAgent(buddy.app.agent, [], "ok")
    await run_case(buddy, Case("t", "hello"), agent)
    request = agent.requests[0]
    assert (request.conversation, request.trading_date, request.message) == ("u1", DAY, "hello")


async def test_a_tool_token_dies_with_its_chat(buddy: Harness) -> None:
    agent = ScriptedAgent(buddy.app.agent, [("get_plan", {})], "ok")
    await run_case(buddy, Case("t", "x"), agent)
    assert agent.results[0]["plan"] is None
    with pytest.raises(ToolTokenInvalid):
        await buddy.app.agent.run_tool(agent.requests[0].tool_token, "get_plan", {})
    with pytest.raises(ToolTokenInvalid):
        await buddy.app.agent.run_tool("made-up", "get_plan", {})


async def test_each_chat_gets_its_own_token(buddy: Harness) -> None:
    agent = ScriptedAgent(buddy.app.agent, [], "ok")
    await run_case(buddy, Case("t", "one"), agent)
    await run_case(buddy, Case("t", "two"), agent)
    assert agent.requests[0].tool_token != agent.requests[1].tool_token


async def test_the_card_on_screen_comes_from_core(buddy: Harness) -> None:
    """Whatever the agent's text says, the proposal shown is the signal core's tool approved."""
    case = Case("t", "show me", calls=[SIGNAL, PROPOSE])
    signal_id = await prepare(buddy, case)
    agent = ScriptedAgent(buddy.app.agent, script(case, signal_id), "Entry at 1, stop at 2.")
    reply, _ = await run_case(buddy, case, agent)
    assert reply.proposal is not None
    assert (reply.proposal.id, reply.proposal.entry) == (signal_id, Decimal("100"))


async def test_an_unreachable_agent_is_a_calm_reply(buddy: Harness) -> None:
    class Down:
        async def chat(self, request: AgentRequest) -> AgentAnswer:
            raise AgentUnavailable("ConnectError")

    buddy.app.agent.agent = Down()
    reply = await buddy.app.agent.chat(who(buddy), "hello")
    assert "can't be reached" in reply.text
    assert reply.proposal is None
    assert buddy.app.agent._chats == {}  # the token went with the failed chat
