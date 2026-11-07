"""The per-tick trailing loop. Every move goes through OrderService.modify_stop."""

import logging
from datetime import timedelta
from decimal import Decimal

from coolcandle.domain.models import Mode, OrderSource
from coolcandle.domain.trade_state import TradeState
from coolcandle.domain.trailing import next_stop
from coolcandle.ports.clock import Clock
from coolcandle.services.market.candles import CandleBuilder
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.service import OrderService, TradeError

log = logging.getLogger(__name__)
MIN_GAP = timedelta(seconds=2)


class TrailingService:
    def __init__(
        self,
        clock: Clock,
        book: TradeBook,
        orders: OrderService,
        candles: CandleBuilder,
        live_enabled: bool,
    ) -> None:
        self.clock = clock
        self.book = book
        self.orders = orders
        self.candles = candles
        self.live_enabled = live_enabled

    async def on_tick(self, key: str, ltp: Decimal) -> None:
        now = self.clock.now()
        for trade in self.book.for_instrument(key):
            if trade.state is not TradeState.OPEN or trade.trailing is None:
                continue
            if trade.mode is Mode.LIVE and not (
                self.live_enabled and self.orders.brokers.has_live(trade.account_id)
            ):
                continue
            timers = self.book.timers(trade.id)
            if timers.last_trail_at is not None and now - timers.last_trail_at < MIN_GAP:
                continue
            stop = next_stop(trade.trailing, trade, self.candles.closed(key, "1m"), ltp)
            if stop is None:
                continue
            timers.last_trail_at = now
            try:
                await self.orders.modify_stop(trade.id, stop, OrderSource.TRAILING)
            except TradeError as exc:
                log.info("trailing skipped a move: %s", exc.message)
