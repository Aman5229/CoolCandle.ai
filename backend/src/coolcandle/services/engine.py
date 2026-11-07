"""The running system: one tick path and the background loops that keep the day safe."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import date, timedelta
from typing import Any

from coolcandle.domain.calendar import MarketCalendar, to_ist
from coolcandle.domain.models import Mode, Order, OrderRequest, OrderStatus, Tick
from coolcandle.ports.clock import Clock
from coolcandle.ports.market_data import MarketData
from coolcandle.services.accounts.service import AccountService
from coolcandle.services.bus import EventBus
from coolcandle.services.events.service import EventService
from coolcandle.services.guard.monitor import DayMonitor
from coolcandle.services.guard.service import GuardService
from coolcandle.services.market.candles import CandleBuilder
from coolcandle.services.market.feed import FeedManager
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.routing import BrokerRouter
from coolcandle.services.orders.service import OrderService
from coolcandle.services.plans.service import PlanService
from coolcandle.services.reconcile.service import Reconciler
from coolcandle.services.recorder.recorder import Recorder
from coolcandle.services.scheduler.service import Scheduler
from coolcandle.services.signals.service import SignalService
from coolcandle.services.trailing.service import TrailingService

log = logging.getLogger(__name__)

PUMP_EVERY_S = 0.1
MONITOR_EVERY_S = 0.25
HOUSEKEEPING_EVERY_S = 0.5
ORDER_SYNC_EVERY_S = 2.0
RECONCILE_EVERY_S = 5.0
NEWS_EVERY_S = 300.0
HISTORY_DAYS = 10


class Engine:
    def __init__(
        self,
        *,
        clock: Clock,
        calendar: MarketCalendar,
        bus: EventBus,
        quotes: QuoteBook,
        candles: CandleBuilder,
        instruments: InstrumentBook,
        feed: FeedManager,
        recorder: Recorder | None,
        brokers: BrokerRouter,
        book: TradeBook,
        plans: PlanService,
        guard: GuardService,
        orders: OrderService,
        monitor: DayMonitor,
        reconciler: Reconciler,
        trailing: TrailingService,
        signals: SignalService,
        events: EventService,
        accounts: AccountService,
    ) -> None:
        self.clock = clock
        self.calendar = calendar
        self.bus = bus
        self.quotes = quotes
        self.candles = candles
        self.instruments = instruments
        self.feed = feed
        self.recorder = recorder
        self.brokers = brokers
        self.book = book
        self.plans = plans
        self.guard = guard
        self.orders = orders
        self.monitor = monitor
        self.reconciler = reconciler
        self.trailing = trailing
        self.signals = signals
        self.events = events
        self.accounts = accounts
        self.scheduler: Scheduler | None = None
        self.account_ids: list[str] = []
        self.order_wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._feed_task: asyncio.Task[None] | None = None
        self._indices: dict[str, str] = {}
        self._loaded_day: date | None = None

    def modes(self, account_id: str) -> list[Mode]:
        return [Mode.PAPER, Mode.LIVE] if self.brokers.has_live(account_id) else [Mode.PAPER]

    # --- day state -----------------------------------------------------------

    async def load_account(self, account_id: str, mode: Mode) -> None:
        """Rebuild an account's day from the database: restart must keep plan and lock."""
        day = self.calendar.trading_date(self.clock.now())
        await self.book.load(account_id, mode, day)
        await self.guard.load(account_id, mode)
        await self.plans.get(account_id)
        if mode is Mode.PAPER:
            rows = await self.book.day_orders(account_id, mode, day)
            self.brokers.paper(account_id).restore(
                [
                    Order(
                        id=row.broker_order_id,
                        broker_order_id=row.broker_order_id,
                        request=OrderRequest.model_validate(row.request),
                        status=OrderStatus(row.status),
                        filled_qty=row.filled_qty,
                        avg_price=row.avg_price,
                        updated_at=row.updated_at,
                    )
                    for row in rows
                    if row.broker_order_id is not None
                ]
            )

    async def refresh_accounts(self) -> None:
        for account_id in await self.accounts.all_ids():
            if account_id not in self.account_ids:
                self.account_ids.append(account_id)
                await self.load_account(account_id, Mode.PAPER)
                await self.load_account(account_id, Mode.LIVE)
                if self.book.active(account_id, Mode.LIVE):
                    # The stop still rests at the exchange, but nothing is watching this trade.
                    await self.orders.alerts.alert(
                        "Live trade open", "Reconnect the broker now to manage it"
                    )

    async def new_day(self) -> None:
        """09:00 IST: yesterday's candles, paper book and fired signals are done."""
        day = self.calendar.trading_date(self.clock.now())
        if day == self._loaded_day:
            return
        self._loaded_day = day
        self.candles.reset()
        self.brokers.reset_paper()
        await self.events.load(day)
        await self._backfill()
        for account_id in self.account_ids:
            for mode in self.modes(account_id):
                await self.load_account(account_id, mode)

    async def _backfill(self) -> None:
        market = self.feed.market
        if market is None:
            return
        today = to_ist(self.clock.now()).date()
        for index in self.instruments.indices():
            start = today - timedelta(days=HISTORY_DAYS)
            for interval in ("1m", "5m"):
                candles = await market.history(index.key, interval, start, today)
                self.candles.set_history(index.key, interval, candles)

    # --- the tick path -------------------------------------------------------

    async def on_tick(self, tick: Tick) -> None:
        self.quotes.update(tick)
        for _, _, broker in self.brokers.all():
            broker.on_tick(tick)
        closed = self.candles.update(tick)
        day = self.calendar.trading_date(self.clock.now())
        if self.recorder is not None:
            self.recorder.write(day, tick)
        self.bus.publish("tick", tick.model_dump(mode="json"))

        await self.pump()
        await self.orders.on_tick(tick.instrument_key, tick.ltp)
        await self.trailing.on_tick(tick.instrument_key, tick.ltp)

        underlying = self._indices.get(tick.instrument_key)
        if underlying is not None:
            await self.feed.on_index_tick(underlying, tick.ltp, day)
        for candle in closed:
            self.bus.publish("candle", candle.model_dump(mode="json"))
            await self.signals.on_candle(candle, self.account_ids)

    async def pump(self) -> None:
        """Hand every order update from the brokers to OrderService."""
        for account_id, mode, broker in self.brokers.all():
            for update in broker.drain_updates():
                await self.orders.on_order_update(account_id, mode, update)

    async def check_all(self) -> None:
        for account_id in self.account_ids:
            for mode in self.modes(account_id):
                await self.monitor.check(account_id, mode)

    async def reconcile_all(self) -> None:
        for account_id in self.account_ids:
            for mode in self.modes(account_id):
                await self.reconciler.run(account_id, mode)

    async def sync_orders(self) -> None:
        for _, mode, broker in self.brokers.all():
            if mode is Mode.LIVE:
                await broker.sync()
        await self.pump()

    # --- running -------------------------------------------------------------

    async def use_market(self, market: MarketData) -> None:
        """Switch the price source: the simulator locally, Kotak after a broker connect."""
        if self._feed_task is not None:
            self._feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feed_task
        await self.feed.use(market)
        self._indices = {i.key: i.underlying for i in self.instruments.indices()}
        if self.recorder is not None:
            day = self.calendar.trading_date(self.clock.now())
            self.recorder.save_instruments(day, self.instruments.all())
        await self._backfill()
        self._feed_task = asyncio.create_task(self._run_feed(market), name="market-feed")

    async def _run_feed(self, market: MarketData) -> None:
        async for tick in market.ticks():
            try:
                await self.on_tick(tick)
            except Exception:
                log.exception("a tick failed")

    def _every(self, name: str, seconds: float, step: Callable[[], Awaitable[None]]) -> None:
        async def loop() -> None:
            while True:
                try:
                    await step()
                except Exception:
                    log.exception("%s failed", name)
                await asyncio.sleep(seconds)

        self._tasks.append(asyncio.create_task(loop(), name=name))

    def track(self, name: str, job: Coroutine[Any, Any, None]) -> None:
        """Run a job until the engine stops. A new job with the same name replaces the old."""
        previous = self._jobs.pop(name, None)
        if previous is not None:
            previous.cancel()
        self._jobs[name] = asyncio.create_task(job, name=name)

    async def _order_sync_step(self) -> None:
        """Poll on a timer, or at once when the broker's order feed says something changed."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.order_wake.wait(), ORDER_SYNC_EVERY_S)
        self.order_wake.clear()
        await self.sync_orders()

    async def start(self, market: MarketData) -> None:
        await self.refresh_accounts()
        self._loaded_day = self.calendar.trading_date(self.clock.now())
        await self.events.load(self._loaded_day)
        await self.use_market(market)
        self._every("pump", PUMP_EVERY_S, self.pump)
        self._every("monitor", MONITOR_EVERY_S, self.check_all)
        self._every("housekeeping", HOUSEKEEPING_EVERY_S, self.orders.housekeeping)
        self._every("order-sync", 0, self._order_sync_step)
        self._every("reconcile", RECONCILE_EVERY_S, self.reconcile_all)
        self._every("news", NEWS_EVERY_S, self.events.refresh_headlines)
        if self.scheduler is not None:
            self._every("scheduler", 1.0, self.scheduler.tick)

    async def stop(self) -> None:
        tasks = [*self._tasks, *self._jobs.values()]
        if self._feed_task is not None:
            tasks.append(self._feed_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._jobs.clear()
        self._feed_task = None
        if self.recorder is not None:
            self.recorder.close()
