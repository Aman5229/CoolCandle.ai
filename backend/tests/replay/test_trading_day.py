"""Whole flows through the real services on a fake clock: the blueprint's replay layer."""

import asyncio
from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from coolcandle.adapters.brokers.paper.broker import PaperBroker
from coolcandle.db.tables import DayLock, OrderRow
from coolcandle.domain.events import MarketEvent
from coolcandle.domain.guard import DayState, LockReason
from coolcandle.domain.models import (
    ExitReason,
    Intent,
    Mode,
    Order,
    OrderChanges,
    OrderRequest,
    OrderSource,
    OrderStatus,
    OrderType,
    Side,
    Tick,
    TrailingRule,
    TrailKind,
)
from coolcandle.domain.trade_state import TradeState
from coolcandle.main import build
from coolcandle.ports.broker import BrokerError, BrokerUnknownOutcome
from coolcandle.services.orders.service import OrderBlocked, TradeError
from tests.conftest import CALL, DAY, LOT, NIFTY, FakeMarket, Harness, at, make_harness


def paper(harness: Harness) -> PaperBroker:
    broker = harness.app.brokers.paper(harness.account_id)
    assert isinstance(broker, PaperBroker)
    return broker


async def lock_row(harness: Harness) -> DayLock | None:
    async with harness.app.db() as session:
        return (await session.execute(select(DayLock))).scalar_one_or_none()


async def view_state(harness: Harness) -> DayState:
    view = await harness.app.guard.view(harness.account_id, harness.mode)
    return view.status.state


# --- entry and stop ----------------------------------------------------------


async def test_a_stop_goes_to_the_exchange_the_moment_the_entry_fills(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()

    assert trade.state is TradeState.OPEN
    assert trade.entry_avg == Decimal("100")
    orders = await paper(harness).orders()
    stop = next(o for o in orders if o.request.side is Side.SELL)
    assert stop.status is OrderStatus.OPEN
    assert stop.request.order_type is OrderType.SL
    assert stop.request.qty == LOT
    assert stop.request.trigger == Decimal("90")
    assert stop.request.price == Decimal("88.20")


async def test_an_entry_rests_until_the_ask_reaches_the_limit(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    trade = await harness.app.orders.submit_entry(
        harness.account_id, Mode.PAPER, harness.ticket(limit_price=Decimal("100"))
    )
    await harness.app.engine.pump()
    assert harness.app.book.get(trade.id).state is TradeState.ENTRY_PENDING  # type: ignore[union-attr]

    await harness.tick(CALL, "100", "99.95", "100")
    assert harness.app.book.get(trade.id).state is TradeState.OPEN  # type: ignore[union-attr]


async def test_a_pending_entry_can_be_cancelled(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    trade = await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    await harness.app.engine.pump()
    await harness.app.orders.cancel_entry(trade.id)
    await harness.app.engine.pump()

    assert harness.active() == []
    assert (await harness.trades())[0].state is TradeState.CANCELLED


async def test_no_entry_without_a_locked_plan(harness: Harness) -> None:
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100")
    with pytest.raises(OrderBlocked) as blocked:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert blocked.value.code == "NO_PLAN"


async def test_a_double_click_cannot_open_two_trades(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    orders = harness.app.orders
    results = await asyncio.gather(
        orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket()),
        orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket()),
        return_exceptions=True,
    )
    assert sum(isinstance(r, OrderBlocked) for r in results) == 1
    assert len(harness.active()) == 1


async def test_a_blocked_order_never_reaches_the_broker(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100")
    with pytest.raises(OrderBlocked):
        await harness.app.orders.submit_entry(
            harness.account_id, Mode.PAPER, harness.ticket(lots=50)
        )
    assert await paper(harness).orders() == []


# --- exits -------------------------------------------------------------------


async def test_the_stop_closes_the_trade(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.tick(CALL, "89.9", "89.85", "89.95")

    closed = (await harness.trades())[0]
    assert closed.id == trade.id
    assert closed.state is TradeState.CLOSED
    assert closed.exit_reason is ExitReason.STOP
    assert closed.realised_pnl == (Decimal("89.85") - Decimal("100")) * LOT


async def test_the_target_exit_modifies_the_stop_and_never_cancels_it(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter(target=Decimal("120"))
    await harness.tick(CALL, "120", "119.95", "120.05")
    await harness.app.engine.pump()

    closed = (await harness.trades())[0]
    assert closed.state is TradeState.CLOSED
    assert closed.exit_reason is ExitReason.TARGET
    orders = await paper(harness).orders()
    sells = [o for o in orders if o.request.side is Side.SELL]
    assert len(sells) == 1  # the same order, turned into the exit
    assert sells[0].status is OrderStatus.COMPLETE
    assert not any(o.status is OrderStatus.CANCELLED for o in orders)
    assert trade.stop_order_id == closed.stop_order_id


async def test_a_manual_exit(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.tick(CALL, "105", "104.95", "105.05")
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.app.engine.pump()

    closed = (await harness.trades())[0]
    assert closed.exit_reason is ExitReason.MANUAL
    assert closed.exit_avg == Decimal("104.95")


async def test_every_transition_is_journalled(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.tick(CALL, "89.9", "89.85", "89.95")
    kinds = [e.type for e in await harness.app.journal.timeline(trade.id)]
    assert "ENTRY_FILLED" in kinds
    assert "STOP_PLACED" in kinds
    assert kinds[-1] == "EXIT_FILLED"


# --- the lock ----------------------------------------------------------------


async def test_the_loss_limit_locks_the_day_and_exits(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    # A gap straight through the stop: the SL-limit cannot fill at its limit.
    await harness.tick(CALL, "40", "39", "41")
    await harness.app.engine.check_all()
    await harness.app.engine.pump()

    row = await lock_row(harness)
    assert row is not None
    assert row.reason == LockReason.LOSS_LIMIT
    assert harness.active() == []
    closed = (await harness.trades())[0]
    assert closed.exit_reason is ExitReason.DAY_LOCK
    assert await view_state(harness) is DayState.LOCKED


async def test_the_profit_target_locks_the_day_and_exits(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    await harness.tick(CALL, "200", "199.95", "200.05")
    await harness.settle()

    row = await lock_row(harness)
    assert row is not None
    assert row.reason == LockReason.TARGET
    assert harness.active() == []


async def test_max_trades_locks_the_day_once_flat(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.settle()
    assert await lock_row(harness) is None  # still in the trade

    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()
    row = await lock_row(harness)
    assert row is not None
    assert row.reason == LockReason.MAX_TRADES


async def test_square_off_flattens_and_locks(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    harness.clock.set(at(15, 15))
    await harness.tick(CALL, "101", "100.95", "101.05")
    await harness.settle()

    row = await lock_row(harness)
    assert row is not None
    assert row.reason == LockReason.SQUARE_OFF
    assert (await harness.trades())[0].exit_reason is ExitReason.SQUARE_OFF


async def test_a_locked_day_blocks_every_new_entry(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()

    await harness.tick(CALL, "100", "99.95", "100")
    with pytest.raises(OrderBlocked) as blocked:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert blocked.value.code == "DAY_LOCKED"


async def test_a_lock_cancels_a_pending_entry(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    await harness.app.engine.pump()

    harness.clock.set(at(15, 15))
    await harness.settle()
    assert harness.active() == []
    assert (await harness.trades())[0].state is TradeState.CANCELLED


async def test_a_lock_fires_once(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    await harness.tick(CALL, "200", "199.95", "200.05")
    for _ in range(3):
        await harness.settle()
    async with harness.app.db() as session:
        rows = (await session.execute(select(DayLock))).scalars().all()
    assert len(rows) == 1


async def test_paper_and_live_locks_are_separate(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()
    assert harness.app.guard.lock_reason(harness.account_id, Mode.PAPER) is not None
    assert harness.app.guard.lock_reason(harness.account_id, Mode.LIVE) is None


async def test_removing_broker_credentials_cannot_dodge_a_lock(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()

    await harness.app.accounts.save_credentials(
        harness.account_id, "kotak_neo", "Kotak", {"consumer_key": "x"}
    )
    await harness.app.accounts.clear_credentials(harness.account_id)

    await harness.tick(CALL, "100", "99.95", "100")
    with pytest.raises(OrderBlocked):
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())


# --- restart -----------------------------------------------------------------


async def reopen(harness: Harness, url: str) -> Harness:
    """Build a fresh process on the same database, as a crash and restart would."""
    await harness.app.db.kw["bind"].dispose()
    app = build(harness.app.settings.model_copy(update={"database_url": url}), clock=harness.clock)
    app.engine.recorder = None
    await app.engine.feed.use(FakeMarket())
    app.engine._indices = {NIFTY: "NIFTY"}
    await app.engine.refresh_accounts()
    return Harness(app=app, clock=harness.clock, account_id=harness.account_id)


@pytest.fixture
async def on_disk(tmp_path: Path) -> tuple[Harness, str]:
    url = f"sqlite+aiosqlite:///{tmp_path}/day.db"
    return await make_harness(url), url


async def test_a_restart_keeps_the_plan_and_the_lock(on_disk: tuple[Harness, str]) -> None:
    harness, url = on_disk
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()

    after = await reopen(harness, url)
    assert (await after.app.plans.get(after.account_id)) is not None
    assert after.app.guard.lock_reason(after.account_id, Mode.PAPER) is LockReason.MAX_TRADES
    await after.tick(NIFTY, "24500")
    await after.tick(CALL, "100", "99.95", "100")
    with pytest.raises(OrderBlocked):
        await after.app.orders.submit_entry(after.account_id, Mode.PAPER, after.ticket())
    await after.app.db.kw["bind"].dispose()


async def test_a_restart_keeps_an_open_trade_and_its_stop(on_disk: tuple[Harness, str]) -> None:
    harness, url = on_disk
    await harness.lock_plan()
    trade = await harness.enter()

    after = await reopen(harness, url)
    assert [t.id for t in after.active()] == [trade.id]
    await after.tick(NIFTY, "24500")
    await after.tick(CALL, "89.9", "89.85", "89.95")
    assert after.active() == []
    closed = (await after.trades())[0]
    assert closed.exit_reason is ExitReason.STOP
    await after.app.db.kw["bind"].dispose()


async def test_a_restart_keeps_the_day_counts(on_disk: tuple[Harness, str]) -> None:
    harness, url = on_disk
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.app.engine.pump()

    after = await reopen(harness, url)
    snapshot = await after.app.guard.snapshot(after.account_id, Mode.PAPER)
    assert snapshot.trades_taken == 1
    await after.app.db.kw["bind"].dispose()


async def test_a_crash_after_the_lock_row_still_finishes_the_exit(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    snapshot = await harness.app.guard.snapshot(harness.account_id, Mode.PAPER)
    # Only the lock row was written; the exit never ran.
    await harness.app.guard.write_lock(
        harness.account_id, Mode.PAPER, LockReason.LOSS_LIMIT, snapshot
    )
    assert len(harness.active()) == 1

    await harness.tick(CALL, "95", "94.95", "95.05")
    await harness.settle()
    assert harness.active() == []


# --- reconcile ---------------------------------------------------------------


async def test_reconcile_replaces_a_missing_stop(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    broker = paper(harness)
    stop_id = harness.app.book.broker_id(trade.stop_order_id)
    assert stop_id is not None
    await broker.cancel_order(stop_id)
    broker.drain_updates()  # the update never arrives

    await harness.app.engine.reconciler.run(harness.account_id, Mode.PAPER)
    await harness.app.engine.pump()

    live = [
        o
        for o in await broker.orders()
        if o.status is OrderStatus.OPEN and o.request.order_type is OrderType.SL
    ]
    assert len(live) == 1


async def test_a_cancelled_stop_is_replaced_at_once(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    broker = paper(harness)
    stop_id = harness.app.book.broker_id(trade.stop_order_id)
    assert stop_id is not None
    await broker.cancel_order(stop_id)
    await harness.app.engine.pump()

    live = [
        o
        for o in await broker.orders()
        if o.status is OrderStatus.OPEN and o.request.order_type is OrderType.SL
    ]
    assert len(live) == 1


async def test_reconcile_closes_a_position_already_gone(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    broker = paper(harness)
    await harness.tick(CALL, "89.9", "89.85", "89.95")
    # The fill happened, but imagine we missed the update: rebuild the trade as open.
    closed = (await harness.trades())[0]
    reopened = closed.model_copy(
        update={"state": TradeState.OPEN, "exit_avg": None, "closed_at": None, "exit_reason": None}
    )
    harness.app.book._active[reopened.id] = reopened

    await harness.app.engine.reconciler.run(harness.account_id, Mode.PAPER)
    assert harness.active() == []
    assert any(o.status is OrderStatus.COMPLETE for o in await broker.orders())


# --- partial fills -----------------------------------------------------------


async def test_a_partial_fill_gets_a_stop_for_the_filled_quantity(harness: Harness) -> None:
    await harness.lock_plan()
    broker = paper(harness)
    broker._fill_qty = lambda order: LOT if order.filled_qty == 0 else 0  # type: ignore[method-assign]
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "20", "19.95", "20")
    trade = await harness.app.orders.submit_entry(
        harness.account_id,
        Mode.PAPER,
        harness.ticket(lots=2, limit_price=Decimal("20"), stop_trigger=Decimal("18")),
    )
    await harness.app.engine.pump()

    current = harness.app.book.get(trade.id)
    assert current is not None
    assert current.state is TradeState.OPEN
    assert current.filled_qty == LOT
    stop = next(o for o in await broker.orders() if o.request.side is Side.SELL)
    assert stop.request.qty == LOT


async def test_the_rest_of_a_partial_fill_is_cancelled_after_30_seconds(harness: Harness) -> None:
    await harness.lock_plan()
    broker = paper(harness)
    broker._fill_qty = lambda order: LOT if order.filled_qty == 0 else 0  # type: ignore[method-assign]
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "20", "19.95", "20")
    trade = await harness.app.orders.submit_entry(
        harness.account_id,
        Mode.PAPER,
        harness.ticket(lots=2, limit_price=Decimal("20"), stop_trigger=Decimal("18")),
    )
    await harness.app.engine.pump()

    harness.clock.advance(29)
    await harness.settle()
    entry_id = harness.app.book.broker_id(trade.entry_order_id)
    orders = {o.broker_order_id: o for o in await broker.orders()}
    assert orders[entry_id].status is OrderStatus.OPEN  # type: ignore[index]

    harness.clock.advance(2)
    await harness.settle()
    orders = {o.broker_order_id: o for o in await broker.orders()}
    assert orders[entry_id].status is OrderStatus.CANCELLED  # type: ignore[index]
    current = harness.app.book.get(trade.id)
    assert current is not None and current.state is TradeState.OPEN


async def test_a_top_up_fill_grows_the_stop(harness: Harness) -> None:
    await harness.lock_plan()
    broker = paper(harness)
    broker._fill_qty = lambda order: LOT  # type: ignore[method-assign]
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "20", "19.95", "20.05")
    await harness.tick(CALL, "20", "19.95", "20")
    await harness.app.orders.submit_entry(
        harness.account_id,
        Mode.PAPER,
        harness.ticket(lots=2, limit_price=Decimal("20"), stop_trigger=Decimal("18")),
    )
    await harness.app.engine.pump()
    await harness.tick(CALL, "20", "19.95", "20")
    await harness.app.engine.pump()

    stop = next(o for o in await broker.orders() if o.request.side is Side.SELL)
    assert stop.request.qty == LOT * 2


# --- stops that misbehave ----------------------------------------------------


async def test_a_refused_stop_means_exit_now(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "99.95", "100")
    trade = await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    # The market falls under the trigger before the entry's fill is processed.
    now = harness.clock.now()
    harness.app.quotes.update(
        Tick(
            instrument_key=CALL,
            ts=now,
            received_at=now,
            ltp=Decimal("85"),
            bid=Decimal("84.95"),
            ask=Decimal("85.05"),
        )
    )
    await harness.app.engine.pump()
    await harness.app.engine.pump()
    await harness.tick(CALL, "85", "84.95", "85.05")
    await harness.app.engine.pump()

    assert harness.app.book.get(trade.id) is None
    closed = (await harness.trades())[0]
    assert closed.state is TradeState.CLOSED
    assert closed.exit_reason is ExitReason.STOP


async def test_a_crossed_stop_that_will_not_fill_is_chased(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.enter()
    await harness.tick(CALL, "85", "84", "86")  # bid is under the SL limit of 88.20
    assert len(harness.active()) == 1

    harness.clock.advance(2)
    await harness.settle()
    assert harness.active() == []
    assert (await harness.trades())[0].exit_reason is ExitReason.STOP


async def test_an_exit_that_will_not_fill_is_repriced_then_flagged(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.tick(CALL, "105", "104.95", "105.05")
    broker = paper(harness)
    broker._fill_qty = lambda order: 0  # type: ignore[method-assign]

    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.app.engine.pump()
    assert harness.active()[0].state is TradeState.EXITING

    await harness.tick(CALL, "103", "102.95", "103.05")
    harness.clock.advance(2)
    await harness.settle()
    stop = next(o for o in await broker.orders() if o.request.side is Side.SELL)
    assert stop.request.price == Decimal("102.95")

    harness.clock.advance(9)
    await harness.settle()
    assert harness.active()[0].state is TradeState.NEEDS_ATTENTION


async def test_needs_attention_blocks_new_entries_but_not_the_exit(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.app.orders.flag_attention(trade.id, "test")

    await harness.tick(CALL, "100", "99.95", "100")
    with pytest.raises(OrderBlocked) as blocked:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert "NEEDS_ATTENTION" in {r.code for r in blocked.value.decision.reasons}

    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.app.engine.pump()
    assert harness.active() == []


# --- broker failures ---------------------------------------------------------


class FlakyPlace:
    """Wraps a broker so the next place_order fails in a chosen way."""

    def __init__(self, broker: PaperBroker, error: Exception, placed_anyway: bool) -> None:
        self.broker = broker
        self.error = error
        self.placed_anyway = placed_anyway
        self.real = broker.place_order
        self.calls = 0
        broker.place_order = self  # type: ignore[method-assign]

    async def __call__(self, req: OrderRequest) -> Order:
        self.calls += 1
        if self.calls == 1:
            if self.placed_anyway:
                await self.real(req)
            raise self.error
        return await self.real(req)


async def test_a_refused_entry_is_rejected_and_never_retried(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "99.95", "100")
    flaky = FlakyPlace(paper(harness), BrokerError("Insufficient funds"), placed_anyway=False)
    trade = await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert trade.state is TradeState.REJECTED
    assert flaky.calls == 1


async def test_an_unknown_outcome_finds_the_order_instead_of_placing_again(
    harness: Harness,
) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    flaky = FlakyPlace(paper(harness), BrokerUnknownOutcome("timeout"), placed_anyway=True)
    trade = await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert flaky.calls == 1
    assert trade.state is TradeState.ENTRY_PENDING
    assert len(await paper(harness).orders()) == 1


async def test_an_unknown_outcome_with_no_order_is_flagged(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "100", "101")
    FlakyPlace(paper(harness), BrokerUnknownOutcome("timeout"), placed_anyway=False)
    trade = await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert trade.state is TradeState.NEEDS_ATTENTION


# --- trades made in the broker's own app (D4) --------------------------------


def external_order(side: Side, broker_id: str) -> Order:
    return Order(
        id=broker_id,
        broker_order_id=broker_id,
        request=OrderRequest(
            instrument_key=CALL,
            trading_symbol="NIFTY24500CE",
            exchange_segment="nse_fo",
            side=side,
            qty=LOT,
            order_type=OrderType.LIMIT,
            price=Decimal("100"),
            intent=Intent.ENTRY,
        ),
        status=OrderStatus.COMPLETE,
        filled_qty=LOT,
        avg_price=Decimal("100"),
        updated_at=at(10),
    )


async def test_outside_trades_count_toward_the_limits(harness: Harness) -> None:
    await harness.lock_plan(max_trades=1)
    await harness.app.orders.on_order_update(
        harness.account_id, Mode.PAPER, external_order(Side.BUY, "K1")
    )
    snapshot = await harness.app.guard.snapshot(harness.account_id, Mode.PAPER)
    assert snapshot.trades_taken == 1

    await harness.settle()
    assert harness.app.guard.lock_reason(harness.account_id, Mode.PAPER) is LockReason.MAX_TRADES


async def test_an_outside_order_is_counted_once(harness: Harness) -> None:
    await harness.lock_plan()
    for _ in range(3):
        await harness.app.orders.on_order_update(
            harness.account_id, Mode.PAPER, external_order(Side.BUY, "K1")
        )
    snapshot = await harness.app.guard.snapshot(harness.account_id, Mode.PAPER)
    assert snapshot.trades_taken == 1
    async with harness.app.db() as session:
        rows = (await session.execute(select(OrderRow).where(OrderRow.external))).scalars().all()
    assert len(rows) == 1


async def test_an_outside_exit_is_not_an_entry(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.app.orders.on_order_update(
        harness.account_id, Mode.PAPER, external_order(Side.SELL, "K2")
    )
    snapshot = await harness.app.guard.snapshot(harness.account_id, Mode.PAPER)
    assert snapshot.trades_taken == 0


# --- news risk ---------------------------------------------------------------


async def test_entries_stop_around_a_scheduled_event(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(10, 5)))
    await harness.tick(NIFTY, "24500")
    await harness.tick(CALL, "100", "99.95", "100")
    with pytest.raises(OrderBlocked) as blocked:
        await harness.app.orders.submit_entry(harness.account_id, Mode.PAPER, harness.ticket())
    assert "EVENT_WINDOW" in {r.code for r in blocked.value.decision.reasons}


async def test_an_open_trade_can_still_exit_during_an_event(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(10, 5)))
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.app.engine.pump()
    assert harness.active() == []


async def test_the_event_shows_on_the_day_status(harness: Harness) -> None:
    await harness.lock_plan()
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(14, 0)))
    view = await harness.app.guard.view(harness.account_id, Mode.PAPER)
    assert view.status.state is DayState.WARNING
    assert "RBI policy at 14:00" in view.status.reasons[0].message


async def test_todays_events_cannot_be_removed(harness: Harness) -> None:
    await harness.app.events.add(MarketEvent(day=DAY, name="RBI policy", at=time(14, 0)))
    assert not await harness.app.events.remove(DAY, "RBI policy", DAY)
    assert await harness.app.events.load(DAY)


# --- trailing (V4) -----------------------------------------------------------


async def test_a_trailing_rule_moves_the_stop_through_the_one_door(harness: Harness) -> None:
    await harness.lock_plan()
    rule = TrailingRule(kind=TrailKind.BREAKEVEN, trigger_points=Decimal("10"))
    trade = await harness.enter(trailing=rule)
    await harness.tick(CALL, "111", "110.95", "111.05")

    current = harness.app.book.get(trade.id)
    assert current is not None
    assert current.stop_trigger == Decimal("100")
    assert current.stop_moved
    stop = next(o for o in await paper(harness).orders() if o.request.side is Side.SELL)
    assert stop.request.trigger == Decimal("100")


async def test_trailing_moves_at_most_once_every_two_seconds(harness: Harness) -> None:
    await harness.lock_plan()
    rule = TrailingRule(kind=TrailKind.STEP, step_points=Decimal("5"), trail_points=Decimal("3"))
    trade = await harness.enter(trailing=rule)
    await harness.tick(CALL, "106", "105.95", "106.05")
    await harness.tick(CALL, "111", "110.95", "111.05")
    assert harness.app.book.get(trade.id).stop_trigger == Decimal("102")  # type: ignore[union-attr]

    harness.clock.advance(2)
    await harness.tick(CALL, "111", "110.95", "111.05")
    assert harness.app.book.get(trade.id).stop_trigger == Decimal("107")  # type: ignore[union-attr]


async def test_a_trailed_stop_closes_as_trailing(harness: Harness) -> None:
    await harness.lock_plan()
    rule = TrailingRule(kind=TrailKind.BREAKEVEN, trigger_points=Decimal("10"))
    await harness.enter(trailing=rule)
    await harness.tick(CALL, "111", "110.95", "111.05")
    await harness.tick(CALL, "99.9", "99.85", "99.95")

    assert (await harness.trades())[0].exit_reason is ExitReason.TRAILING


async def test_a_target_trail_rides_past_the_target(harness: Harness) -> None:
    await harness.lock_plan()
    rule = TrailingRule(kind=TrailKind.TARGET_TRAIL, trail_points=Decimal("5"))
    trade = await harness.enter(target=Decimal("120"), trailing=rule)
    await harness.tick(CALL, "121", "120.95", "121.05")

    current = harness.app.book.get(trade.id)
    assert current is not None
    assert current.state is TradeState.OPEN
    assert current.stop_trigger == Decimal("116")


async def live_trailed_trade(harness: Harness) -> str:
    """A live trade on a stand-in broker, so only the live switch decides what happens."""
    stand_in = PaperBroker(harness.clock, harness.app.quotes.get, Decimal("500000"))
    harness.app.brokers.set_live(harness.account_id, stand_in)
    harness.mode = Mode.LIVE
    await harness.lock_plan()
    rule = TrailingRule(kind=TrailKind.BREAKEVEN, trigger_points=Decimal("10"))
    trade = await harness.enter(trailing=rule)
    await harness.tick(CALL, "111", "110.95", "111.05")
    return trade.id


async def test_trailing_stays_off_for_live_trades_by_default(harness: Harness) -> None:
    trade_id = await live_trailed_trade(harness)
    assert harness.app.book.get(trade_id).stop_trigger == Decimal("90")  # type: ignore[union-attr]


async def test_trailing_runs_live_once_it_is_switched_on(harness: Harness) -> None:
    harness.app.engine.trailing.live_enabled = True
    trade_id = await live_trailed_trade(harness)
    assert harness.app.book.get(trade_id).stop_trigger == Decimal("100")  # type: ignore[union-attr]


async def test_a_manual_stop_can_only_tighten(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()
    with pytest.raises(OrderBlocked):
        await harness.app.orders.modify_stop(trade.id, Decimal("85"), OrderSource.MANUAL)
    moved = await harness.app.orders.modify_stop(trade.id, Decimal("95"), OrderSource.MANUAL)
    assert moved.stop_trigger == Decimal("95")


async def test_broker_modify_failure_is_reported(harness: Harness) -> None:
    await harness.lock_plan()
    trade = await harness.enter()

    async def refuse(_: str, __: OrderChanges) -> Order:
        raise BrokerError("Order is closed")

    paper(harness).modify_order = refuse  # type: ignore[method-assign,assignment]
    with pytest.raises(TradeError) as failed:
        await harness.app.orders.modify_stop(trade.id, Decimal("95"), OrderSource.MANUAL)
    assert failed.value.code == "MODIFY_FAILED"


# --- a restart with a live trade open ----------------------------------------


async def test_a_restart_warns_about_a_live_trade_and_leaves_it_alone(
    on_disk: tuple[Harness, str],
) -> None:
    harness, url = on_disk
    stand_in = PaperBroker(harness.clock, harness.app.quotes.get, Decimal("500000"))
    harness.app.brokers.set_live(harness.account_id, stand_in)
    harness.mode = Mode.LIVE
    await harness.lock_plan()
    await harness.enter()

    alerts: list[str] = []
    after = await reopen_watching(harness, url, alerts)
    assert len(after.app.book.active(after.account_id, Mode.LIVE)) == 1
    assert alerts == ["Live trade open"]

    # No session yet: ticks and timers must pass over it, not crash on it.
    await after.tick(NIFTY, "24500")
    await after.tick(CALL, "500", "499.95", "500.05")
    await after.app.orders.housekeeping()
    assert len(after.app.book.active(after.account_id, Mode.LIVE)) == 1
    await after.app.db.kw["bind"].dispose()


async def reopen_watching(harness: Harness, url: str, alerts: list[str]) -> Harness:
    await harness.app.db.kw["bind"].dispose()
    app = build(harness.app.settings.model_copy(update={"database_url": url}), clock=harness.clock)
    app.engine.recorder = None
    await app.engine.feed.use(FakeMarket())
    app.engine._indices = {NIFTY: "NIFTY"}

    async def record(title: str, body: str, link: str | None = None) -> None:
        alerts.append(title)

    app.orders.alerts.alert = record  # type: ignore[method-assign]
    await app.engine.refresh_accounts()
    return Harness(app=app, clock=harness.clock, account_id=harness.account_id, mode=Mode.LIVE)


async def test_a_restart_keeps_the_live_lock_before_the_broker_reconnects(
    on_disk: tuple[Harness, str],
) -> None:
    harness, url = on_disk
    await harness.lock_plan()
    snapshot = await harness.app.guard.snapshot(harness.account_id, Mode.LIVE)
    await harness.app.guard.write_lock(
        harness.account_id, Mode.LIVE, LockReason.LOSS_LIMIT, snapshot
    )
    after = await reopen(harness, url)
    assert after.app.guard.lock_reason(after.account_id, Mode.LIVE) is LockReason.LOSS_LIMIT
    await after.app.db.kw["bind"].dispose()
