"""Compares our trades with the broker's truth, and fixes or flags any difference."""

from coolcandle.domain.models import (
    FINAL_ORDER_STATUSES,
    Mode,
    Order,
    OrderStatus,
    Side,
    Trade,
)
from coolcandle.domain.trade_state import TradeState
from coolcandle.ports.broker import BrokerError
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.routing import BrokerRouter
from coolcandle.services.orders.service import OrderService


def _last_sell(trade: Trade, orders: list[Order]) -> Order | None:
    sells = [
        o
        for o in orders
        if o.request.instrument_key == trade.instrument_key
        and o.request.side is Side.SELL
        and o.status is OrderStatus.COMPLETE
    ]
    return sells[-1] if sells else None


class Reconciler:
    def __init__(self, book: TradeBook, brokers: BrokerRouter, orders: OrderService) -> None:
        self.book = book
        self.brokers = brokers
        self.orders = orders

    async def run(self, account_id: str, mode: Mode) -> None:
        active = [t for t in self.book.active(account_id, mode) if t.state is TradeState.OPEN]
        if not active:
            return
        broker = self.brokers.get(account_id, mode)
        try:
            positions = await broker.positions()
            orders = await broker.orders()
        except BrokerError:
            return  # the broker is unreachable, so there is nothing to compare with
        held_by_key = {p.instrument_key: p.net_qty for p in positions}
        by_id = {o.broker_order_id: o for o in orders}

        for trade in active:
            held = held_by_key.get(trade.instrument_key, 0)
            stop_id = self.book.broker_id(trade.stop_order_id)
            stop = by_id.get(stop_id) if stop_id else None

            if held <= 0:
                fill = _last_sell(trade, orders)
                if fill is not None:
                    await self.orders.close_from_broker(trade.id, fill)
                else:
                    await self.orders.flag_attention(trade.id, "Position gone with no exit fill")
            elif stop is None or stop.status in FINAL_ORDER_STATUSES:
                await self.orders.ensure_stop(trade.id)
            elif stop.request.qty != held:
                await self.orders.resize_stop(trade.id, held)
