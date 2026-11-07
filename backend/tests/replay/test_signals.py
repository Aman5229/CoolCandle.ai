"""V2 and V3 together: a setup fires, a signal appears, and only a tap can enter it."""

from datetime import time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from coolcandle.domain.events import MarketEvent
from coolcandle.domain.models import EntryTicket, ExitReason, Mode, OrderSource
from coolcandle.domain.signals import Signal, SignalStatus
from coolcandle.domain.trade_state import TradeState
from coolcandle.services.orders.service import OrderBlocked, TradeError
from tests.conftest import CALL, DAY, NIFTY, Harness, at


async def breakout(harness: Harness) -> None:
    """A 24500 to 24520 opening range, then a close above it at 09:35."""
    start = at(9, 15)
    for minute in range(21):
        harness.clock.set(start + timedelta(minutes=minute))
        if minute < 15:
            price = ("24500", "24520")[minute % 2]
        elif minute < 20:
            price = "24530"
        else:
            price = "24531"
        await harness.tick(NIFTY, price)
        await harness.tick(CALL, "100", "99.95", "100")


async def live_signal(harness: Harness) -> Signal:
    await harness.lock_plan()
    await breakout(harness)
    signal = await harness.app.signals.current(harness.account_id)
    assert signal is not None
    return signal


async def take(harness: Harness, signal: Signal, lots: int | None = 1) -> Any:
    return await harness.app.signals.take(
        harness.account_id, Mode.PAPER, signal.id, lots, OrderSource.SIGNAL
    )


async def test_a_breakout_raises_one_signal_with_code_owned_numbers(harness: Harness) -> None:
    signal = await live_signal(harness)
    assert signal.trading_symbol == "NIFTY24500CE"
    assert signal.entry == Decimal("100")
    assert signal.stop == Decimal("90.00")  # 20 index points at a delta of 0.5
    assert signal.target == Decimal("120.00")
    assert len(await harness.app.signals.today(harness.account_id)) == 1


async def test_a_signal_places_nothing_on_its_own(harness: Harness) -> None:
    await live_signal(harness)
    assert await harness.trades() == []


async def test_a_setup_fires_once_a_day(harness: Harness) -> None:
    await live_signal(harness)
    harness.clock.set(at(9, 37))
    await breakout(harness)
    assert len(await harness.app.signals.today(harness.account_id)) == 1


async def test_no_signal_without_a_locked_plan(harness: Harness) -> None:
    await breakout(harness)
    assert await harness.app.signals.today(harness.account_id) == []


async def test_no_signal_on_a_locked_day(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    harness.clock.set(at(9, 21))
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()

    await breakout(harness)
    assert await harness.app.signals.today(harness.account_id) == []


async def test_no_signal_around_a_scheduled_event(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(9, 35)))
    await breakout(harness)
    assert await harness.app.signals.today(harness.account_id) == []


async def test_a_tap_enters_the_trade_through_the_guard(harness: Harness) -> None:
    signal = await live_signal(harness)
    trade = await take(harness, signal)
    await harness.app.engine.pump()

    current = harness.app.book.get(trade.id)
    assert current is not None
    assert current.state is TradeState.OPEN
    assert current.source is OrderSource.SIGNAL
    assert current.target == Decimal("120.00")
    assert (await harness.app.signals.get(harness.account_id, signal.id)).status is (
        SignalStatus.TAKEN
    )


async def test_a_tap_with_no_lots_uses_the_sized_quantity(harness: Harness) -> None:
    signal = await live_signal(harness)
    trade = await take(harness, signal, None)
    assert trade.qty == 65


async def test_a_signal_cannot_be_taken_twice(harness: Harness) -> None:
    signal = await live_signal(harness)
    await take(harness, signal)
    with pytest.raises(TradeError) as refused:
        await take(harness, signal)
    assert refused.value.code == "SIGNAL_USED"


def signal_ticket(signal: Signal, token: str | None) -> EntryTicket:
    return EntryTicket(
        instrument_key=signal.instrument_key,
        lots=1,
        limit_price=signal.entry,
        stop_trigger=signal.stop,
        source=OrderSource.SIGNAL,
        signal_id=signal.id,
        confirm_token=token,
    )


@pytest.mark.parametrize("source", [OrderSource.SIGNAL, OrderSource.AGENT])
async def test_no_token_means_no_trade(harness: Harness, source: OrderSource) -> None:
    signal = await live_signal(harness)
    ticket = signal_ticket(signal, None).model_copy(update={"source": source})
    with pytest.raises(TradeError) as refused:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, ticket)
    assert refused.value.code == "CONFIRM_REQUIRED"
    assert await harness.trades() == []


async def test_a_made_up_token_is_refused(harness: Harness) -> None:
    signal = await live_signal(harness)
    with pytest.raises(TradeError) as refused:
        await harness.app.orders.submit_entry(
            harness.account_id, Mode.PAPER, signal_ticket(signal, "guess")
        )
    assert refused.value.code == "CONFIRM_REQUIRED"


async def test_a_token_works_once(harness: Harness) -> None:
    signal = await live_signal(harness)
    token = harness.app.orders.tokens.issue(signal.id, harness.clock.now())
    ticket = signal_ticket(signal, token)
    await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, ticket)
    with pytest.raises(TradeError) as refused:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, ticket)
    assert refused.value.code == "CONFIRM_REQUIRED"


async def test_a_token_for_another_signal_is_refused(harness: Harness) -> None:
    signal = await live_signal(harness)
    token = harness.app.orders.tokens.issue("some-other-signal", harness.clock.now())
    with pytest.raises(TradeError):
        await harness.app.orders.submit_entry(
            harness.account_id, Mode.PAPER, signal_ticket(signal, token)
        )


async def test_a_token_expires(harness: Harness) -> None:
    signal = await live_signal(harness)
    token = harness.app.orders.tokens.issue(signal.id, harness.clock.now())
    harness.clock.advance(61)
    with pytest.raises(TradeError):
        await harness.app.orders.submit_entry(
            harness.account_id, Mode.PAPER, signal_ticket(signal, token)
        )


async def test_an_expired_signal_is_refused(harness: Harness) -> None:
    signal = await live_signal(harness)
    harness.clock.set(signal.expires_at + timedelta(seconds=1))
    assert await harness.app.signals.current(harness.account_id) is None
    with pytest.raises(TradeError) as refused:
        await take(harness, signal)
    assert refused.value.code == "SIGNAL_EXPIRED"


async def test_a_price_that_left_the_band_is_refused(harness: Harness) -> None:
    signal = await live_signal(harness)
    await harness.tick(CALL, "110", "109.95", "110.05")
    with pytest.raises(TradeError) as refused:
        await take(harness, signal)
    assert refused.value.code == "SIGNAL_MOVED"


async def test_the_guard_still_has_the_last_word_after_a_tap(harness: Harness) -> None:
    signal = await live_signal(harness)
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(9, 36)))
    with pytest.raises(OrderBlocked) as blocked:
        await take(harness, signal)
    assert "EVENT_WINDOW" in {r.code for r in blocked.value.decision.reasons}


async def test_a_declined_signal_is_gone(harness: Harness) -> None:
    signal = await live_signal(harness)
    await harness.app.signals.decline(harness.account_id, signal.id)
    assert await harness.app.signals.current(harness.account_id) is None


async def test_another_accounts_signal_is_invisible(harness: Harness) -> None:
    signal = await live_signal(harness)
    with pytest.raises(TradeError) as refused:
        await harness.app.signals.get("someone-else", signal.id)
    assert refused.value.code == "SIGNAL_NOT_FOUND"


def test_alert_links_are_signed_and_expire(harness: Harness) -> None:
    notify = harness.app.notify
    now = at(10)
    link = notify.signed_link("s1", now + timedelta(minutes=10))
    expires = int(link.split("e=")[1].split("&")[0])
    signature = link.split("s=")[1]
    assert notify.verify_link("s1", expires, signature, now)
    assert not notify.verify_link("s2", expires, signature, now)
    assert not notify.verify_link("s1", expires, "forged", now)
    assert not notify.verify_link("s1", expires, signature, now + timedelta(minutes=11))
