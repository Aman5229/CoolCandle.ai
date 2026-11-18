"""What Skill Test needs from the shared paper broker: shorts, buy stops above the price, market
fills with slippage. Live paper trading sends none of these, and its own tests stay unchanged."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from coolcandle.adapters.brokers.paper.broker import PaperBroker
from coolcandle.adapters.clock import ManualClock
from coolcandle.domain.models import (
    Intent,
    OrderChanges,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    Tick,
)
from coolcandle.ports.broker import BrokerError

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
KEY = "skill|NIFTY|2025-11-11|25500|CE"


def tick(ltp: str, bid: str | None = None, ask: str | None = None) -> Tick:
    return Tick(
        instrument_key=KEY,
        ts=NOW,
        received_at=NOW,
        ltp=Decimal(ltp),
        bid=Decimal(bid) if bid else None,
        ask=Decimal(ask) if ask else None,
    )


class Market:
    def __init__(self) -> None:
        self.now = tick("100", "99.9", "100.1")

    def quote(self, key: str) -> Tick | None:
        return self.now


def broker(market: Market, slippage: str = "0") -> PaperBroker:
    return PaperBroker(
        ManualClock(NOW), market.quote, Decimal("100000"), slippage=Decimal(slippage)
    )


def order(
    side: Side,
    order_type: OrderType,
    *,
    price: str | None = None,
    trigger: str | None = None,
    intent: Intent = Intent.ENTRY,
) -> OrderRequest:
    return OrderRequest(
        instrument_key=KEY,
        trading_symbol="BTCUSDT",
        exchange_segment="BINANCE",
        side=side,
        qty=5,
        order_type=order_type,
        price=Decimal(price) if price else None,
        trigger=Decimal(trigger) if trigger else None,
        intent=intent,
        trade_id="s1",
    )


async def test_a_market_buy_fills_at_the_ask_plus_slippage() -> None:
    placed = await broker(Market(), "0.01").place_order(order(Side.BUY, OrderType.MARKET))
    assert (placed.status, placed.avg_price) == (OrderStatus.COMPLETE, Decimal("100.11"))


async def test_a_short_opens_with_a_market_sell_at_the_bid_less_slippage() -> None:
    paper = broker(Market(), "0.01")
    placed = await paper.place_order(order(Side.SELL, OrderType.MARKET))
    assert (placed.status, placed.avg_price) == (OrderStatus.COMPLETE, Decimal("99.89"))
    [position] = await paper.positions()
    assert position.net_qty == -5


async def test_without_slippage_a_market_order_fills_exactly_at_the_quote() -> None:
    placed = await broker(Market()).place_order(order(Side.BUY, OrderType.MARKET))
    assert placed.avg_price == Decimal("100.1")


async def test_a_buy_stop_above_the_price_waits_then_fires_at_market() -> None:
    market = Market()
    paper = broker(market, "0.01")
    stop = await paper.place_order(
        order(Side.BUY, OrderType.SL_M, trigger="105", intent=Intent.STOP)
    )
    assert stop.status is OrderStatus.OPEN

    market.now = tick("104.9", "104.8", "105")
    paper.on_tick(market.now)
    assert (await paper.orders())[0].status is OrderStatus.OPEN

    market.now = tick("105.2", "105.1", "105.3")
    paper.on_tick(market.now)
    fired = (await paper.orders())[0]
    assert (fired.status, fired.avg_price) == (OrderStatus.COMPLETE, Decimal("105.31"))


async def test_a_sell_stop_below_the_price_fires_at_or_below_its_trigger() -> None:
    market = Market()
    paper = broker(market)
    await paper.place_order(order(Side.SELL, OrderType.SL_M, trigger="95", intent=Intent.STOP))
    market.now = tick("95", "94.9", "95.1")
    paper.on_tick(market.now)
    fired = (await paper.orders())[0]
    assert (fired.status, fired.avg_price) == (OrderStatus.COMPLETE, Decimal("94.9"))


@pytest.mark.parametrize(
    ("side", "trigger", "message"),
    [
        (Side.BUY, "100", "below the last traded price"),
        (Side.BUY, "99", "below the last traded price"),
        (Side.SELL, "100", "above the last traded price"),
        (Side.SELL, "101", "above the last traded price"),
    ],
)
async def test_a_stop_already_through_the_price_is_refused(
    side: Side, trigger: str, message: str
) -> None:
    placed = await broker(Market()).place_order(
        order(side, OrderType.SL_M, trigger=trigger, intent=Intent.STOP)
    )
    assert placed.status is OrderStatus.REJECTED
    assert placed.reject_reason is not None and message in placed.reject_reason


async def test_an_exit_turns_the_stop_into_a_market_order_without_cancelling_it() -> None:
    """The same rule as live: exits modify the stop, never cancel it and then send a market."""
    market = Market()
    paper = broker(market)
    stop = await paper.place_order(
        order(Side.BUY, OrderType.SL_M, trigger="105", intent=Intent.STOP)
    )
    exited = await paper.modify_order(
        stop.broker_order_id, OrderChanges(order_type=OrderType.MARKET)
    )
    assert (exited.status, exited.avg_price) == (OrderStatus.COMPLETE, Decimal("100.1"))


async def test_a_limit_order_still_needs_a_price() -> None:
    with pytest.raises(BrokerError):
        await broker(Market()).place_order(order(Side.BUY, OrderType.LIMIT))
