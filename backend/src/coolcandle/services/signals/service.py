"""V2: runs setups on closed candles, stores signals, and takes one only after a tap."""

import logging
from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import SignalRow, new_id
from coolcandle.domain.calendar import MarketCalendar, to_ist
from coolcandle.domain.events import blocking_event
from coolcandle.domain.models import Candle, EntryTicket, Mode, OrderSource, Trade
from coolcandle.domain.plan import PlanStatus
from coolcandle.domain.setups.base import Direction, SetupHit
from coolcandle.domain.signals import (
    Signal,
    SignalConfig,
    SignalStatus,
    build_signal,
    option_side,
    within_band,
)
from coolcandle.ports.clock import Clock
from coolcandle.ports.signal_source import SignalSource
from coolcandle.services.bus import EventBus
from coolcandle.services.events.service import EventService
from coolcandle.services.guard.service import GuardService
from coolcandle.services.market.candles import CandleBuilder
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook
from coolcandle.services.notify.service import NotifyService
from coolcandle.services.orders.service import OrderService, TradeError
from coolcandle.services.orders.tokens import ConfirmTokens
from coolcandle.services.plans.service import PlanService

log = logging.getLogger(__name__)


def _to_signal(row: SignalRow) -> Signal:
    return Signal(
        id=row.id,
        setup=row.setup,
        setup_version=row.setup_version,
        underlying=row.underlying,
        direction=Direction(row.direction),
        instrument_key=row.instrument_key,
        trading_symbol=row.trading_symbol,
        lot_size=row.lot_size,
        entry=row.entry,
        stop=row.stop,
        target=row.target,
        expires_at=row.expires_at,
        reason=row.reason,
        features=row.features,
        status=SignalStatus(row.status),
    )


class SignalService:
    def __init__(
        self,
        *,
        db: Sessions,
        clock: Clock,
        calendar: MarketCalendar,
        setups: list[SignalSource],
        candles: CandleBuilder,
        instruments: InstrumentBook,
        quotes: QuoteBook,
        orders: OrderService,
        plans: PlanService,
        guard: GuardService,
        events: EventService,
        tokens: ConfirmTokens,
        notify: NotifyService,
        bus: EventBus,
        config: SignalConfig,
    ) -> None:
        self.db = db
        self.clock = clock
        self.calendar = calendar
        self.setups = setups
        self.candles = candles
        self.instruments = instruments
        self.quotes = quotes
        self.orders = orders
        self.plans = plans
        self.guard = guard
        self.events = events
        self.tokens = tokens
        self.notify = notify
        self.bus = bus
        self.config = config
        self.listeners: list[Callable[[str, Signal], Awaitable[None]]] = []
        self._fired: set[tuple[str, str, str, date]] = set()

    def _day(self) -> date:
        return self.calendar.trading_date(self.clock.now())

    async def on_candle(self, candle: Candle, account_ids: list[str]) -> None:
        if candle.interval != "5m":
            return
        index = next(
            (i for i in self.instruments.indices() if i.key == candle.instrument_key), None
        )
        if index is None:
            return
        today_candles = [
            c
            for c in self.candles.closed(index.key, "5m")
            if to_ist(c.start).date() == to_ist(candle.start).date()
        ]
        history = self.candles.history(index.key, "5m")
        for setup in self.setups:
            hit = setup.check(index.underlying, today_candles, history)
            if hit is not None:
                for account_id in account_ids:
                    await self._raise_signal(account_id, hit)

    async def _raise_signal(self, account_id: str, hit: SetupHit) -> None:
        day = self._day()
        once = (account_id, hit.setup, hit.underlying, day)
        if once in self._fired:
            return  # one trigger per setup per day
        self._fired.add(once)

        # No alerts that tempt a locked or unplanned day, or the minutes around big news.
        plan = await self.plans.get(account_id)
        if plan is None or plan.status is not PlanStatus.LOCKED:
            return
        if any(self.guard.lock_reason(account_id, mode) for mode in Mode):
            return
        if blocking_event(self.events.on(day), self.clock.now()) is not None:
            return

        option = self.instruments.atm_option(
            hit.underlying, hit.close, option_side(hit.direction), day
        )
        quote = self.quotes.get(option.key) if option else None
        if option is None or quote is None:
            log.info("no option quote for %s", hit.setup)
            return
        signal = build_signal(new_id(), hit, option, quote, self.config)
        if signal is None:
            return
        async with self.db() as session, session.begin():
            session.add(
                SignalRow(
                    id=signal.id,
                    broker_account_id=account_id,
                    trading_date=day,
                    setup=signal.setup,
                    setup_version=signal.setup_version,
                    underlying=signal.underlying,
                    direction=signal.direction,
                    instrument_key=signal.instrument_key,
                    trading_symbol=signal.trading_symbol,
                    lot_size=signal.lot_size,
                    entry=signal.entry,
                    stop=signal.stop,
                    target=signal.target,
                    expires_at=signal.expires_at,
                    reason=signal.reason,
                    features=signal.features,
                    status=signal.status,
                )
            )
        self.bus.publish("signal", signal.model_dump(mode="json"))
        valid = to_ist(signal.expires_at)
        await self.notify.alert(
            f"{signal.trading_symbol} ~{signal.entry}",
            f"stop {signal.stop} · target {signal.target} · valid till {valid:%H:%M}",
            self.notify.signed_link(signal.id, signal.expires_at),
        )
        for listener in self.listeners:
            await listener(account_id, signal)

    async def today(self, account_id: str) -> list[Signal]:
        async with self.db() as session:
            rows = await session.execute(
                select(SignalRow)
                .where(
                    SignalRow.broker_account_id == account_id,
                    SignalRow.trading_date == self._day(),
                )
                .order_by(SignalRow.created_at.desc())
            )
        return [_to_signal(row) for row in rows.scalars()]

    async def current(self, account_id: str) -> Signal | None:
        now = self.clock.now()
        for signal in await self.today(account_id):
            if signal.status is SignalStatus.NEW and signal.expires_at > now:
                return signal
        return None

    async def get(self, account_id: str, signal_id: str) -> Signal:
        async with self.db() as session:
            row = await session.get(SignalRow, signal_id)
        if row is None or row.broker_account_id != account_id:
            raise TradeError("SIGNAL_NOT_FOUND", "No such signal")
        return _to_signal(row)

    async def _set_status(self, signal_id: str, status: SignalStatus) -> None:
        async with self.db() as session, session.begin():
            row = await session.get(SignalRow, signal_id)
            if row is not None:
                row.status = status

    async def decline(self, account_id: str, signal_id: str) -> None:
        await self.get(account_id, signal_id)
        await self._set_status(signal_id, SignalStatus.DECLINED)

    async def take(
        self, account_id: str, mode: Mode, signal_id: str, lots: int | None, source: OrderSource
    ) -> Trade:
        """Called by a tap on screen, so this is the only place a token gets created."""
        signal = await self.get(account_id, signal_id)
        now = self.clock.now()
        if signal.status is not SignalStatus.NEW:
            raise TradeError("SIGNAL_USED", "That signal was already handled")
        if signal.expires_at <= now:
            await self._set_status(signal_id, SignalStatus.EXPIRED)
            raise TradeError("SIGNAL_EXPIRED", "That signal has expired")
        tick = self.quotes.get(signal.instrument_key)
        price = tick.ask if tick is not None and tick.ask is not None else None
        if price is None and tick is not None:
            price = tick.ltp
        if price is None or not within_band(signal, price, self.config.band_pct):
            raise TradeError("SIGNAL_MOVED", "The price has left the signal's band")

        ticket = EntryTicket(
            instrument_key=signal.instrument_key,
            lots=lots or 1,
            limit_price=price,
            stop_trigger=signal.stop,
            target=signal.target,
            source=source,
            signal_id=signal.id,
            confirm_token=self.tokens.issue(signal.id, now),
        )
        if lots is None:
            preview = await self.orders.preview(account_id, mode, ticket)
            size = preview.size.lots if preview.size is not None else 0
            if size < 1:
                raise TradeError("NO_SIZE", "Not even one lot fits today's limits")
            ticket = ticket.model_copy(update={"lots": size})
        trade = await self.orders.submit_entry(account_id, mode, ticket)
        await self._set_status(signal_id, SignalStatus.TAKEN)
        return trade
