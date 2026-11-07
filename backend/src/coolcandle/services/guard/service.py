"""Builds the day's snapshot, applies the pure guard, and owns the day locks."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from coolcandle.db.session import Sessions
from coolcandle.db.tables import DayLock
from coolcandle.domain.calendar import MarketCalendar
from coolcandle.domain.costs import CostRates, estimate
from coolcandle.domain.guard import (
    DayStatus,
    GuardConfig,
    LockReason,
    OrderContext,
    day_status,
    evaluate,
)
from coolcandle.domain.models import (
    ZERO,
    DaySnapshot,
    Decision,
    Mode,
    OrderRequest,
    Position,
)
from coolcandle.domain.plan import DailyPlan
from coolcandle.domain.trade_state import TradeState
from coolcandle.ports.clock import Clock
from coolcandle.services.events.service import EventService
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.routing import BrokerRouter, BrokerUnavailable
from coolcandle.services.plans.service import PlanService


@dataclass(frozen=True)
class DayView:
    status: DayStatus
    snapshot: DaySnapshot
    plan: DailyPlan | None
    locked_at: datetime | None
    lock_ends_at: datetime | None


class GuardService:
    def __init__(
        self,
        *,
        db: Sessions,
        clock: Clock,
        calendar: MarketCalendar,
        plans: PlanService,
        book: TradeBook,
        quotes: QuoteBook,
        instruments: InstrumentBook,
        brokers: BrokerRouter,
        events: EventService,
        rates: CostRates,
        config: GuardConfig,
    ) -> None:
        self.db = db
        self.clock = clock
        self.calendar = calendar
        self.plans = plans
        self.book = book
        self.quotes = quotes
        self.instruments = instruments
        self.brokers = brokers
        self.events = events
        self.rates = rates
        self.config = config
        self._locks: dict[tuple[str, Mode, date], tuple[LockReason, datetime]] = {}

    def today(self) -> date:
        return self.calendar.trading_date(self.clock.now())

    def lock_reason(self, account_id: str, mode: Mode) -> LockReason | None:
        lock = self._locks.get((account_id, mode, self.today()))
        return lock[0] if lock else None

    async def load(self, account_id: str, mode: Mode) -> None:
        day = self.calendar.trading_date(self.clock.now())
        async with self.db() as session:
            row = (
                await session.execute(
                    select(DayLock).where(
                        DayLock.broker_account_id == account_id,
                        DayLock.trading_date == day,
                        DayLock.mode == mode,
                    )
                )
            ).scalar_one_or_none()
        if row is not None:
            self._locks[(account_id, mode, day)] = (LockReason(row.reason), row.locked_at)

    async def write_lock(
        self, account_id: str, mode: Mode, reason: LockReason, snapshot: DaySnapshot
    ) -> bool:
        """Write the lock row first, so a crash after this still leaves the day locked."""
        day = self.calendar.trading_date(self.clock.now())
        now = self.clock.now()
        try:
            async with self.db() as session, session.begin():
                session.add(
                    DayLock(
                        broker_account_id=account_id,
                        trading_date=day,
                        mode=mode,
                        reason=reason,
                        snapshot=snapshot.model_dump(mode="json"),
                        locked_at=now,
                    )
                )
        except IntegrityError:
            await self.load(account_id, mode)
            return False
        self._locks[(account_id, mode, day)] = (reason, now)
        return True

    async def _positions(self, account_id: str, mode: Mode) -> list[Position]:
        try:
            return await self.brokers.get(account_id, mode).positions()
        except BrokerUnavailable:
            return []

    async def snapshot(self, account_id: str, mode: Mode) -> DaySnapshot:
        now = self.clock.now()
        day = self.calendar.trading_date(now)
        counters = self.book.counters(account_id, mode, day)
        positions = await self._positions(account_id, mode)

        total = unrealised = buy_value = sell_value = ZERO
        for position in positions:
            tick = self.quotes.get(position.instrument_key)
            cost = position.buy_value / position.buy_qty if position.buy_qty else ZERO
            mark = tick.ltp if tick else cost
            total += position.pnl(mark)
            unrealised += position.net_qty * (mark - cost)
            buy_value += position.buy_value
            sell_value += position.sell_value

        charges = estimate(buy_value, sell_value, counters.executed_orders, self.rates)
        net = total - charges
        counters.peak_net = max(counters.peak_net, net)
        return DaySnapshot(
            realised_pnl=total - unrealised,
            unrealised_pnl=unrealised,
            est_charges=charges,
            net_pnl=net,
            peak_net_pnl=counters.peak_net,
            trades_taken=counters.trades_taken,
            open_trade=bool(self.book.active(account_id, mode)),
            last_loss_at=counters.last_loss_at,
            feed_age=self.quotes.feed_age(now),
            now=now,
        )

    async def evaluate(
        self,
        account_id: str,
        mode: Mode,
        request: OrderRequest,
        *,
        current_stop: Decimal | None = None,
    ) -> Decision:
        plan = await self.plans.get(account_id)
        snapshot = await self.snapshot(account_id, mode)
        day = self.calendar.trading_date(snapshot.now)
        tick = self.quotes.get(request.instrument_key)
        active = self.book.active(account_id, mode)
        context = OrderContext(
            mode=mode,
            rates=self.rates,
            instrument=self.instruments.get(request.instrument_key),
            ltp=tick.ltp if tick else None,
            lock_reason=self.lock_reason(account_id, mode),
            has_open_trade=bool(active),
            needs_attention=any(t.state is TradeState.NEEDS_ATTENTION for t in active),
            session_valid=self.brokers.has_live(account_id),
            market_open=self.calendar.is_market_open(snapshot.now),
            live_orders_today=self.book.counters(account_id, mode, day).live_orders,
            events=self.events.on(day),
            current_stop=current_stop,
        )
        return evaluate(plan, snapshot, request, context, self.config)

    async def view(self, account_id: str, mode: Mode) -> DayView:
        plan = await self.plans.get(account_id)
        snapshot = await self.snapshot(account_id, mode)
        day = self.calendar.trading_date(snapshot.now)
        lock = self._locks.get((account_id, mode, day))
        status = day_status(plan, snapshot, lock[0] if lock else None, self.events.on(day))
        return DayView(
            status=status,
            snapshot=snapshot,
            plan=plan,
            locked_at=lock[1] if lock else None,
            lock_ends_at=self.calendar.lock_ends_at(day) if lock else None,
        )
