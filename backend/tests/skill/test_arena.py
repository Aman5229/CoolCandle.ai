"""A replayed NIFTY day from plan to lock: options at the model's price, the stop placed at the
fill, stop before target inside one minute, the square-off, the score, and a run's "today" that
only moves forward. Each minute plays as four ticks (open, high, low, close)."""

from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from coolcandle.domain.calendar import ist_datetime, to_ist
from coolcandle.domain.models import Intent, OptionType, OrderStatus, OrderType
from coolcandle.domain.plan import PlanError
from coolcandle.domain.skill.models import (
    ExitKind,
    PlanStatus,
    RunStatus,
    SessionState,
    SkillLock,
)
from coolcandle.domain.skill.options import option_key
from coolcandle.domain.skill.score import Line
from coolcandle.services.skill.service import NewSession, SkillBlocked, SkillError, SkillTicket
from tests.skill.conftest import (
    DAY,
    MINUTE,
    NEXT_DAY,
    Skill,
    at,
    flat,
    minute,
    plan,
    seed,
)

TICKS = 4  # a minute


def day_with(*moves: Any) -> list[Any]:
    """Five quiet minutes at 25000 from 09:15, then `moves` (o, h, l, c) one a minute, then quiet
    at the last close until 09:45."""
    candles = flat(DAY, at(9, 15), 5, "25000")
    clock = ist_datetime(DAY, at(9, 20))
    for o, h, low, c in moves:
        candles.append(minute(DAY, to_ist(clock).time(), o, h, low, c))
        clock += MINUTE
    last = moves[-1][3] if moves else "25000"
    left = 30 - len(candles)
    return candles + flat(DAY, to_ist(clock).time(), left, last)


def reseed(skill: Skill, *moves: Any) -> None:
    skill.history.minutes.pop("NIFTY", None)
    seed(skill.history, day_with(*moves))


async def to_minute(skill: Skill, minutes: int, tick: int = 0) -> None:
    """Play on from 09:15's open to tick `tick` of the minute `minutes` after 09:15, or until
    the session locks: a lock stops the replay."""
    arenas = skill.service.arenas
    await skill.gate.step(
        TICKS * minutes + tick, done=lambda: not arenas or any(a.final for a in arenas.values())
    )


async def test_a_session_runs_on_the_runs_today_with_model_prices(skill: Skill) -> None:
    run = await skill.run()
    assert run.today == DAY
    view = await skill.start()
    assert (view.day, view.expiry, view.lot_size) == (DAY, NEXT_DAY, 65)
    assert view.plan_status is PlanStatus.STARTED
    chain = await skill.service.chain(skill.user, view.id)
    assert (chain.spot, chain.atm, chain.vol, chain.vol_source) == (
        Decimal("25000"),
        Decimal("25000"),
        Decimal("0.14"),
        "INDIA_VIX",
    )
    assert len(chain.rows) == 21
    atm = next(r for r in chain.rows if r.strike == chain.atm)
    assert atm.ce.trading_symbol == "NIFTY 11 NOV 25000 CE"
    assert Decimal("60") < atm.ce.ltp < Decimal("120")  # a day-old ATM call at 14% volatility
    assert atm.ce.bid < atm.ce.ltp < atm.ce.ask
    deep = next(r for r in chain.rows if r.strike == chain.atm - 500)
    assert deep.ce.ltp > Decimal("500") > deep.pe.ltp  # in the money is worth more


async def test_the_stop_rests_with_the_broker_and_the_day_squares_off(skill: Skill) -> None:
    await skill.run()
    view = await skill.start()
    quote = await skill.option(view.id)
    view = await skill.buy(view.id, quote)
    trade = view.trades[0]
    assert trade.status == "OPEN"
    assert trade.entry == quote.ask + Decimal("0.05")  # the ask, plus a tick of slippage
    assert trade.qty == 65
    broker = skill.service.arenas[view.id].broker
    stop = next(o for o in await broker.orders() if o.request.intent is Intent.STOP)
    assert stop.status is OrderStatus.OPEN
    assert stop.request.trigger == quote.ask - 20

    arena = skill.service.arenas[view.id]
    await skill.gate.step(TICKS * 27, done=lambda: arena.final)  # to the 09:42 square-off
    view = await skill.view(view.id)
    assert (view.state, view.lock_reason) == (SessionState.LOCKED, SkillLock.SQUARE_OFF)
    assert view.trades[0].exit_kind is ExitKind.LOCK
    assert (await skill.service.runs(skill.user))[0].today == NEXT_DAY
    card = await skill.service.report(skill.user, view.id)
    assert card.trades == 1


async def test_a_target_fills_and_the_profit_target_locks_the_day(skill: Skill) -> None:
    reseed(skill, ("25000", "25300", "25000", "25300"))
    await skill.run()
    view = await skill.start(profit_target="2000")
    view = await skill.buy(view.id, await skill.option(view.id))
    await to_minute(skill, 5, 1)  # the minute's high
    view = await skill.view(view.id)
    assert view.trades[0].exit_kind is ExitKind.TARGET
    assert view.trades[0].pnl > Decimal("2000")
    assert view.lock_reason is SkillLock.TARGET
    lines = [line.line for line in view.score.lines]
    assert Line.TARGET_LOCK in lines


async def test_a_fall_through_the_stop_sells_at_the_bid(skill: Skill) -> None:
    reseed(skill, ("25000", "25000", "24930", "24930"))
    await skill.run()
    view = await skill.start()
    quote = await skill.option(view.id)
    view = await skill.buy(view.id, quote)
    await to_minute(skill, 5, 1)  # the minute's low
    trade = (await skill.view(view.id)).trades[0]
    assert trade.exit_kind is ExitKind.STOP
    assert trade.exit is not None and trade.exit < quote.ask - 20
    assert trade.pnl < 0
    target = next(
        o
        for o in await skill.service.arenas[view.id].broker.orders()
        if o.request.intent is Intent.EXIT
    )
    assert target.status is OrderStatus.CANCELLED  # one-cancels-the-other


@pytest.mark.parametrize(
    ("kind", "candle"),
    [
        (OptionType.CE, ("25000", "25200", "24900", "25150")),  # rising: high first unless...
        (OptionType.PE, ("25000", "25100", "24800", "24850")),  # falling: low first unless...
    ],
)
async def test_when_stop_and_target_share_a_minute_the_stop_fills_first(
    skill: Skill, kind: OptionType, candle: tuple[str, str, str, str]
) -> None:
    reseed(skill, candle)
    await skill.run()
    view = await skill.start()
    view = await skill.buy(view.id, await skill.option(view.id, kind))
    await to_minute(skill, 6)
    trade = (await skill.view(view.id)).trades[0]
    assert trade.exit_kind is ExitKind.STOP
    assert trade.pnl < 0


async def test_an_exit_turns_the_stop_into_a_market_order(skill: Skill) -> None:
    await skill.run()
    view = await skill.start()
    view = await skill.buy(view.id, await skill.option(view.id))
    view = await skill.service.exit(skill.user, view.trades[0].id)
    assert view.trades[0].exit_kind is ExitKind.MANUAL
    stops = [
        o
        for o in await skill.service.arenas[view.id].broker.orders()
        if o.request.intent is Intent.STOP
    ]
    assert len(stops) == 1
    assert stops[0].status is OrderStatus.COMPLETE
    assert stops[0].request.order_type is OrderType.MARKET  # modified, never cancelled


async def ask_of(skill: Skill, session_id: str, strike_offset: int = 0) -> tuple[str, Decimal]:
    chain = await skill.service.chain(skill.user, session_id)
    row = next(r for r in chain.rows if r.strike == chain.atm + strike_offset)
    return row.ce.key, row.ce.ask


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"stop": None}, "NO_STOP"),
        ({"stop": "+5"}, "STOP_SIDE"),
        ({"stop": "-0.1"}, "STOP_TOO_CLOSE"),
        ({"lots": 3}, "TRADE_TOO_BIG"),
        ({"lots": 0}, "LOTS"),
        ({"target": "+10"}, "REWARD_RISK"),
        ({"instrument_key": "skill|NIFTY|2025-11-18|25000|CE"}, "UNKNOWN_OPTION"),
        ({"instrument_key": "skill|NIFTY|2025-11-11|25025|CE"}, "UNKNOWN_OPTION"),
        ({"instrument_key": "skill|SENSEX|2025-11-13|82000|CE"}, "UNKNOWN_OPTION"),
    ],
)
async def test_an_entry_is_refused_with_a_reason_and_counted(
    skill: Skill, changes: dict[str, Any], code: str
) -> None:
    await skill.run()
    view = await skill.start()
    key, ask = await ask_of(skill, view.id)
    values: dict[str, Any] = {
        "instrument_key": key,
        "lots": 1,
        "stop": ask - 20,
        "target": ask + 40,
    }
    for field in ("stop", "target"):
        given = changes.get(field)
        if isinstance(given, str) and given[0] in "+-":
            changes = changes | {field: ask + Decimal(given)}
    with pytest.raises(SkillBlocked) as refused:
        await skill.service.enter(skill.user, view.id, SkillTicket.model_validate(values | changes))
    assert code in [r.code for r in refused.value.decision.reasons]
    view = await skill.view(view.id)
    assert view.trades == ()
    assert view.score.score == 95


async def test_a_deep_in_the_money_option_needs_the_funds_to_pay_for_it(skill: Skill) -> None:
    await skill.run("50000")
    view = await skill.start(max_loss_per_trade="5000")
    key = option_key(
        "NIFTY", NEXT_DAY, Decimal("24000"), OptionType.CE
    )  # off the chain, on the grid
    quote = skill.service.arenas[view.id].option_quote(key)
    assert quote is not None and quote.ask * 65 > 50000
    with pytest.raises(SkillBlocked) as refused:
        await skill.buy(view.id, quote)
    assert [r.code for r in refused.value.decision.reasons] == ["FUNDS"]


async def test_entries_wait_for_the_window_and_a_pause_refuses_them(skill: Skill) -> None:
    await skill.run()
    view = await skill.start(entry_start="09:20")
    with pytest.raises(SkillBlocked) as early:
        await skill.buy(view.id, await skill.option(view.id))
    assert "SESSION_ENTRIES_CLOSED" in [r.code for r in early.value.decision.reasons]
    await to_minute(skill, 5)
    await skill.service.replay_control(skill.user, view.id, "pause", None)
    with pytest.raises(SkillBlocked) as paused:
        await skill.buy(view.id, await skill.option(view.id))
    assert [r.code for r in paused.value.decision.reasons] == ["PRICES_STALE"]
    await skill.service.replay_control(skill.user, view.id, "play", None)
    view = await skill.buy(view.id, await skill.option(view.id))
    assert view.trades[0].status == "OPEN"
    assert view.score.score == 90  # two refused attempts


async def test_widening_the_stop_is_allowed_and_costs_15_points(skill: Skill) -> None:
    await skill.run()
    view = await skill.start()
    quote = await skill.option(view.id)
    view = await skill.buy(view.id, quote)
    trade_id = view.trades[0].id
    view = await skill.service.move_stop(skill.user, trade_id, quote.ask - 30)
    assert view.trades[0].stop_widened
    assert view.score.score == 85
    view = await skill.service.move_stop(skill.user, trade_id, quote.ask - 10)
    assert view.trades[0].stop_widened  # tightening later doesn't undo it
    with pytest.raises(SkillError) as wrong:
        await skill.service.move_stop(skill.user, trade_id, quote.ask + 10)
    assert wrong.value.code == "STOP_SIDE"


async def test_a_played_day_never_comes_back(skill: Skill) -> None:
    await skill.run()
    first = await skill.start()
    await skill.service.end(skill.user, first.id)
    run = (await skill.service.runs(skill.user))[0]
    assert run.today == NEXT_DAY
    skill.gate.parked = 0
    second = await skill.start()
    assert second.day == NEXT_DAY
    await skill.service.end(skill.user, second.id)
    with pytest.raises(SkillError) as over:
        await skill.service.create_session(skill.user, NewSession(index="NIFTY", plan=plan()))
    assert over.value.code == "NO_DAY"


@pytest.mark.parametrize(
    ("day", "today"),
    [
        (date(2025, 11, 8), DAY),  # a Saturday starts on the Monday
        (DAY, DAY),
    ],
)
async def test_a_run_starts_on_the_next_trading_day(skill: Skill, day: date, today: date) -> None:
    assert (await skill.run(day=day)).today == today


@pytest.mark.parametrize("day", [date(2025, 9, 1), date(2025, 12, 1)])
async def test_a_run_needs_history_behind_it_and_a_day_ahead(skill: Skill, day: date) -> None:
    with pytest.raises(SkillError) as refused:
        await skill.run(day=day)
    assert refused.value.code == "START_DAY"


async def test_without_history_skill_test_says_how_to_fetch_it(skill: Skill) -> None:
    skill.history.minutes.clear()
    with pytest.raises(SkillError) as refused:
        await skill.run()
    assert refused.value.code == "NO_HISTORY"
    assert "uv run coolcandle fetch-index-history" in refused.value.message
    account = await skill.service.account(skill.user)
    assert (account.indices, account.first_day) == ((), None)


async def test_a_loss_below_the_blown_line_ends_the_run(skill: Skill) -> None:
    reseed(skill, ("25000", "25000", "24400", "24400"))
    queue = skill.h.app.bus.subscribe()
    await skill.run("50000")
    view = await skill.start(loss_limit="20000", max_loss_per_trade="20000")
    await skill.buy(view.id, await skill.option(view.id), lots=6)
    await to_minute(skill, 5, 1)
    view = await skill.view(view.id)
    assert view.lock_reason is SkillLock.BLOWN
    assert view.fund < Decimal("35000")
    assert (await skill.service.runs(skill.user))[0].status is RunStatus.BLOWN
    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait()["type"])
    assert {"skill.run_blown", "skill.locked"} <= set(kinds)


async def test_the_plan_is_confirmed_twice_then_only_gets_stricter(skill: Skill) -> None:
    await skill.run()
    draft = await skill.service.create_session(skill.user, NewSession(index="NIFTY", plan=plan()))
    assert draft.state is SessionState.DRAFT
    with pytest.raises(PlanError):
        await skill.service.confirm(skill.user, draft.id, 2, "START")
    await skill.service.confirm(skill.user, draft.id, 1, None)
    with pytest.raises(PlanError) as wrong:
        await skill.service.confirm(skill.user, draft.id, 2, "start")
    assert wrong.value.code == "CONFIRM_WORD"
    await skill.service.confirm(skill.user, draft.id, 2, "START")
    with pytest.raises(PlanError):
        await skill.service.edit_plan(skill.user, draft.id, plan(loss_limit="6000"))
    view = await skill.service.edit_plan(
        skill.user, draft.id, plan(loss_limit="4000", entry_cutoff="09:30")
    )
    assert (view.plan.loss_limit, view.plan.entry_cutoff) == (Decimal("4000"), time(9, 30))


async def test_the_chart_never_shows_a_minute_past_the_cursor(skill: Skill) -> None:
    spike = minute(DAY, at(9, 35), "25000", "26000", "25000", "25000")
    seed(
        skill.history,
        [*flat(DAY, at(9, 15), 20, "25000"), spike, *flat(DAY, at(9, 36), 9, "25000")],
    )
    await skill.run()
    view = await skill.start()
    await to_minute(skill, 10, 2)  # 09:25:30
    cursor = ist_datetime(DAY, time(9, 25, 30))
    minutes = await skill.service.candles(skill.user, view.id, "1m")
    today = [c for c in minutes if c.start >= ist_datetime(DAY, at(9, 15))]
    assert [to_ist(c.start).time() for c in today][-1] == time(9, 24)
    assert all(c.start + MINUTE <= cursor for c in minutes)
    assert minutes[0].start >= ist_datetime(DAY, at(9, 15)) - timedelta(days=7)
    days = await skill.service.candles(skill.user, view.id, "1d")
    assert len(days) == 41  # forty days before, and today so far
    assert days[-1].high == Decimal("25000")  # the 26,000 spike at 09:35 hasn't played
    hours = await skill.service.candles(skill.user, view.id, "1h")
    assert hours[-1].start == ist_datetime(DAY, at(9, 15))
    with pytest.raises(SkillError):
        await skill.service.candles(skill.user, view.id, "2m")


async def test_a_restart_brings_the_session_back_paused_with_its_stop(skill: Skill) -> None:
    reseed(skill, ("25000", "25000", "25000", "25000"), ("25000", "25000", "24930", "24930"))
    await skill.run()
    view = await skill.start()
    await skill.buy(view.id, await skill.option(view.id))
    await to_minute(skill, 1)
    cursor = skill.service.arenas[view.id].replay.cursor  # type: ignore[union-attr]
    await skill.service.stop()
    skill.service.arenas.clear()

    await skill.service.start()
    arena = skill.service.arenas[view.id]
    assert arena.replay is not None and arena.replay.paused
    assert arena.replay.cursor == cursor
    assert arena.open_trade() is not None
    parked = skill.gate.parked
    await skill.service.replay_control(skill.user, view.id, "play", None)
    await skill.gate.step(TICKS * 6, done=lambda: skill.gate.parked > parked + TICKS * 5)
    trade = (await skill.view(view.id)).trades[0]
    assert trade.exit_kind is ExitKind.STOP  # the restored stop still fills


async def test_ending_early_closes_the_trade_and_costs_no_points(skill: Skill) -> None:
    await skill.run()
    view = await skill.start()
    await skill.buy(view.id, await skill.option(view.id))
    view = await skill.service.end(skill.user, view.id)
    assert (view.state, view.lock_reason) == (SessionState.ENDED, None)
    assert view.trades[0].exit_kind is ExitKind.LOCK
    assert view.score.score == 100
    assert view.id not in skill.service.arenas


async def test_another_users_session_is_not_found(skill: Skill) -> None:
    await skill.run()
    view = await skill.start()
    with pytest.raises(SkillError) as missing:
        await skill.service.view("someone-else", view.id)
    assert missing.value.status == 404
