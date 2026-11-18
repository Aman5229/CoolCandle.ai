"""Skill Test can never touch live trading (docs/skill-test.md §0 and §4.4).

Every test here runs a whole Skill Test session (entry, a moved stop, an exit, a lock) and checks
the live side saw none of it: no rows in its tables, no change to its day, no call to its broker.
The other direction holds too: a live lock never locks a Skill Test session."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text

from coolcandle.domain.guard import DayState, LockReason
from coolcandle.domain.models import DaySnapshot, EntryTicket, Mode, OptionType
from coolcandle.domain.skill.models import ExitKind, SessionState, SkillLock
from coolcandle.domain.skill.options import option_key
from coolcandle.services.orders.service import TradeError
from coolcandle.services.skill.service import NewSession, SkillBlocked, SkillError
from tests.conftest import NIFTY, Harness
from tests.skill.conftest import DAY, NEXT_DAY, Skill, at, flat, minute, plan, seed

LIVE_TABLES = (
    "daily_plans",
    "plan_changes",
    "day_locks",
    "trades",
    "orders",
    "trade_events",
    "audit_log",
    "signals",
)


async def live_rows(h: Harness) -> dict[str, int]:
    counts = {}
    async with h.app.db() as session:
        for table in LIVE_TABLES:
            found = await session.scalar(select(func.count()).select_from(text(table)))
            counts[table] = found or 0
    return counts


async def a_whole_session(skill: Skill) -> str:
    """Two trades: one exited by hand after a widened stop, one stopped out by a fall, then the
    square-off locks the day."""
    skill.history.minutes.pop("NIFTY", None)
    fall = minute(DAY, at(9, 21), "25000", "25000", "24900", "24900")
    seed(
        skill.history, [*flat(DAY, at(9, 15), 6, "25000"), fall, *flat(DAY, at(9, 22), 23, "24900")]
    )
    await skill.run()
    session = await skill.start()
    quote = await skill.option(session.id)
    trade_id = (await skill.buy(session.id, quote)).trades[0].id
    await skill.service.move_stop(skill.user, trade_id, quote.ask - 25)
    await skill.service.exit(skill.user, trade_id)
    await skill.buy(session.id, await skill.option(session.id))
    arena = skill.service.arenas[session.id]
    await skill.gate.step(4 * 30, done=lambda: arena.final)
    with pytest.raises(SkillBlocked):
        await skill.buy(session.id, quote)
    view = await skill.view(session.id)
    assert view.lock_reason is SkillLock.SQUARE_OFF
    assert view.trades[1].exit_kind is ExitKind.STOP
    return session.id


async def test_a_skill_session_leaves_every_live_table_untouched(skill: Skill) -> None:
    before = await live_rows(skill.h)
    await a_whole_session(skill)
    assert await live_rows(skill.h) == before


async def test_a_skill_lock_does_not_change_the_live_or_paper_day(skill: Skill) -> None:
    await skill.h.lock_plan()  # the paper day has a plan
    guard = skill.h.app.guard
    before = {
        mode: (await guard.view(skill.h.account_id, mode)).status
        for mode in (Mode.PAPER, Mode.LIVE)
    }
    await a_whole_session(skill)
    after = {
        mode: (await guard.view(skill.h.account_id, mode)).status
        for mode in (Mode.PAPER, Mode.LIVE)
    }
    assert after == before
    assert before[Mode.PAPER].state is DayState.ACTIVE
    assert not guard.locked(skill.h.account_id, Mode.LIVE)


class KotakSpy:
    """Stands in for a connected live broker. Any use of it fails the test."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.calls.append(name)
        raise AssertionError(f"Skill Test touched the live broker: {name}")


async def test_skill_test_never_calls_the_live_broker(skill: Skill) -> None:
    spy = KotakSpy()
    skill.h.app.brokers.set_live(skill.h.account_id, spy)
    await a_whole_session(skill)
    assert spy.calls == []
    paper = skill.h.app.brokers.paper(skill.h.account_id)
    assert await paper.orders() == []  # nor the live app's own paper broker


async def test_a_skill_test_option_is_unknown_to_live_orders(skill: Skill) -> None:
    h = skill.h
    await h.lock_plan()
    await h.tick(NIFTY, "24500")
    ticket = EntryTicket(
        instrument_key=option_key("NIFTY", NEXT_DAY, Decimal("25000"), OptionType.CE),
        lots=1,
        limit_price=Decimal("100"),
        stop_trigger=Decimal("80"),
    )
    with pytest.raises(TradeError) as unknown:
        await h.app.orders.submit_entry(h.account_id, Mode.PAPER, ticket)
    assert unknown.value.code == "UNKNOWN_INSTRUMENT"
    assert h.app.book.active(h.account_id, Mode.PAPER) == []


async def lock_live(h: Harness) -> None:
    await h.app.guard.write_lock(
        h.account_id, Mode.LIVE, LockReason.LOSS_LIMIT, DaySnapshot(now=h.clock.now())
    )


async def test_while_live_is_locked_skill_test_is_closed_by_default(skill: Skill) -> None:
    await skill.run()
    running = await skill.start()
    await lock_live(skill.h)

    with pytest.raises(SkillBlocked) as refused:
        await skill.buy(running.id, await skill.option(running.id))
    assert [r.code for r in refused.value.decision.reasons] == ["LIVE_LOCKED"]
    view = await skill.view(running.id)
    assert view.live_locked
    assert view.state is SessionState.ACTIVE  # closed to new entries, not locked
    await skill.service.end(skill.user, running.id)
    with pytest.raises(SkillError) as closed:
        await skill.service.create_session(skill.user, NewSession(index="NIFTY", plan=plan()))
    assert closed.value.code == "LIVE_LOCKED"
    assert (await skill.service.account(skill.user)).live_locked


async def test_with_the_setting_on_a_live_lock_only_shows_a_banner(skill: Skill) -> None:
    skill.service.settings = skill.service.settings.model_copy(update={"when_live_locked": True})
    await lock_live(skill.h)
    await skill.run()
    session = await skill.start()
    assert session.live_locked  # the banner
    view = await skill.buy(session.id, await skill.option(session.id))
    assert view.trades[0].status == "OPEN"
    assert view.state is SessionState.ACTIVE
