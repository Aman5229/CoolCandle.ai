"""OrderService: the one door to the broker. Nothing else calls place, modify or cancel."""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from coolcandle.db.tables import new_id
from coolcandle.domain.calendar import MarketCalendar
from coolcandle.domain.costs import CostRates, round_trip
from coolcandle.domain.exits import floor_tick, stop_limit_price
from coolcandle.domain.guard import GuardConfig, slippage_points
from coolcandle.domain.models import (
    Decision,
    EntryTicket,
    ExitReason,
    Frozen,
    Instrument,
    Intent,
    Mode,
    Order,
    OrderChanges,
    OrderRequest,
    OrderSource,
    OrderStatus,
    OrderType,
    Side,
    Trade,
)
from coolcandle.domain.sizing import SizeResult, max_lots, worst_case_loss
from coolcandle.domain.trade_state import TradeEvent, TradeState, next_state
from coolcandle.domain.trailing import trails_past_target
from coolcandle.ports.broker import Broker, BrokerError, BrokerUnknownOutcome
from coolcandle.ports.clock import Clock
from coolcandle.services.audit import Audit
from coolcandle.services.guard.service import GuardService
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook
from coolcandle.services.orders.book import OrderLink, Role, TradeBook
from coolcandle.services.orders.routing import BrokerRouter
from coolcandle.services.orders.tokens import ConfirmTokens
from coolcandle.services.plans.service import PlanService

log = logging.getLogger(__name__)

REPRICE_AFTER = timedelta(seconds=2)
ATTENTION_AFTER = timedelta(seconds=10)
CANCEL_REST_AFTER = timedelta(seconds=30)


class AlertSink(Protocol):
    async def alert(self, title: str, body: str, link: str | None = None) -> None: ...


class TradeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OrderBlocked(TradeError):
    def __init__(self, decision: Decision) -> None:
        first = decision.reasons[0]
        super().__init__(first.code, first.message)
        self.decision = decision


class Preview(Frozen):
    decision: Decision
    size: SizeResult | None
    qty: int
    stop_limit: Decimal
    worst_case: Decimal
    charges: Decimal
    reward_risk: Decimal | None


def _advance(trade: Trade, event: TradeEvent, **changes: object) -> Trade:
    return trade.model_copy(update={"state": next_state(trade.state, event), **changes})


class OrderService:
    def __init__(
        self,
        *,
        clock: Clock,
        calendar: MarketCalendar,
        book: TradeBook,
        guard: GuardService,
        brokers: BrokerRouter,
        quotes: QuoteBook,
        instruments: InstrumentBook,
        plans: PlanService,
        tokens: ConfirmTokens,
        alerts: AlertSink,
        audit: Audit,
        rates: CostRates,
        config: GuardConfig,
    ) -> None:
        self.clock = clock
        self.calendar = calendar
        self.book = book
        self.guard = guard
        self.brokers = brokers
        self.quotes = quotes
        self.instruments = instruments
        self.plans = plans
        self.tokens = tokens
        self.alerts = alerts
        self.audit = audit
        self.rates = rates
        self.config = config
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock(self, account_id: str) -> asyncio.Lock:
        return self._locks[account_id]

    def _broker(self, trade: Trade) -> Broker:
        return self.brokers.get(trade.account_id, trade.mode)

    def _require(self, trade_id: str) -> Trade:
        trade = self.book.get(trade_id)
        if trade is None:
            raise TradeError("TRADE_NOT_FOUND", "That trade is not active")
        return trade

    def _reachable(self, trade: Trade) -> bool:
        """After a restart a live trade waits for the broker to reconnect."""
        return trade.mode is Mode.PAPER or self.brokers.has_live(trade.account_id)

    def _instrument(self, key: str) -> Instrument:
        instrument = self.instruments.get(key)
        if instrument is None:
            raise TradeError("UNKNOWN_INSTRUMENT", "That instrument is not in today's list")
        return instrument

    def _entry_request(
        self, ticket: EntryTicket, instrument: Instrument, trade_id: str | None
    ) -> OrderRequest:
        return OrderRequest(
            instrument_key=instrument.key,
            trading_symbol=instrument.trading_symbol,
            exchange_segment=instrument.exchange_segment,
            side=Side.BUY,
            qty=ticket.lots * instrument.lot_size,
            order_type=OrderType.LIMIT,
            price=ticket.limit_price,
            stop=ticket.stop_trigger,
            target=ticket.target,
            source=ticket.source,
            intent=Intent.ENTRY,
            trade_id=trade_id,
            confirm_token=ticket.confirm_token,
        )

    def _sell_request(
        self,
        trade: Trade,
        intent: Intent,
        order_type: OrderType,
        qty: int,
        price: Decimal,
        trigger: Decimal | None = None,
    ) -> OrderRequest:
        return OrderRequest(
            instrument_key=trade.instrument_key,
            trading_symbol=trade.trading_symbol,
            exchange_segment=trade.exchange_segment,
            side=Side.SELL,
            qty=qty,
            order_type=order_type,
            price=price,
            trigger=trigger,
            source=OrderSource.SYSTEM,
            intent=intent,
            trade_id=trade.id,
        )

    # --- preview -------------------------------------------------------------

    async def preview(self, account_id: str, mode: Mode, ticket: EntryTicket) -> Preview:
        instrument = self._instrument(ticket.instrument_key)
        request = self._entry_request(ticket, instrument, None)
        decision = await self.guard.evaluate(account_id, mode, request)
        limit = stop_limit_price(
            ticket.stop_trigger, instrument.tick_size, self.config.stop_limit_pct
        )
        qty = request.qty
        plan = await self.plans.get(account_id)
        buffer = self.config.slippage_points
        size = None
        if plan is not None:
            buffer = slippage_points(plan, self.guard.events.on(plan.trading_date), self.config)
            snapshot = await self.guard.snapshot(account_id, mode)
            funds = await self.brokers.get(account_id, mode).funds()
            size = max_lots(
                plan,
                snapshot,
                ticket.limit_price,
                limit,
                instrument.lot_size,
                self.rates,
                slippage_points=buffer,
                cash=funds.available,
                freeze_qty=instrument.freeze_qty,
            )
        reward_risk = None
        risk = ticket.limit_price - ticket.stop_trigger
        if ticket.target is not None and risk > 0:
            reward_risk = (ticket.target - ticket.limit_price) / risk
        return Preview(
            decision=decision,
            size=size,
            qty=qty,
            stop_limit=limit,
            worst_case=worst_case_loss(ticket.limit_price, limit, qty, buffer, self.rates),
            charges=round_trip(ticket.limit_price, limit, qty, self.rates),
            reward_risk=reward_risk,
        )

    # --- entry ---------------------------------------------------------------

    async def submit_entry(self, account_id: str, mode: Mode, ticket: EntryTicket) -> Trade:
        async with self._lock(account_id):
            instrument = self._instrument(ticket.instrument_key)
            if ticket.source in (OrderSource.SIGNAL, OrderSource.AGENT):
                subject = ticket.signal_id or ""
                if not self.tokens.consume(ticket.confirm_token, subject, self.clock.now()):
                    raise TradeError("CONFIRM_REQUIRED", "This trade needs a tap on screen")

            trade_id = new_id()
            request = self._entry_request(ticket, instrument, trade_id)
            decision = await self.guard.evaluate(account_id, mode, request)
            if not decision.allowed:
                await self.audit.write(
                    "order_blocked",
                    self.clock.now(),
                    data={"codes": [r.code for r in decision.reasons]},
                )
                raise OrderBlocked(decision)

            day = self.calendar.trading_date(self.clock.now())
            trade = Trade(
                id=trade_id,
                account_id=account_id,
                mode=mode,
                trading_date=day,
                state=TradeState.ENTRY_PENDING,
                instrument_key=instrument.key,
                trading_symbol=instrument.trading_symbol,
                exchange_segment=instrument.exchange_segment,
                tick_size=instrument.tick_size,
                qty=request.qty,
                entry_limit=ticket.limit_price,
                stop_trigger=ticket.stop_trigger,
                stop_limit=stop_limit_price(
                    ticket.stop_trigger, instrument.tick_size, self.config.stop_limit_pct
                ),
                target=ticket.target,
                source=ticket.source,
                trailing=ticket.trailing,
                signal_id=ticket.signal_id,
            )
            await self.book.save(trade, "ENTRY_SUBMITTED", {"limit": str(ticket.limit_price)})
            order_id = await self.book.add_order(trade, Role.ENTRY, request)
            trade = await self.book.save(
                trade.model_copy(update={"entry_order_id": order_id}), "ENTRY_ORDER_CREATED"
            )
            broker = self._broker(trade)
            self.book.counters(account_id, mode, day).live_orders += 1
            try:
                placed = await broker.place_order(request)
            except BrokerUnknownOutcome as exc:
                return await self._adopt_or_flag(trade, order_id, request, str(exc))
            except BrokerError as exc:
                await self.book.fail_order(order_id, str(exc))
                return await self.book.save(
                    _advance(trade, TradeEvent.REJECT), "ENTRY_REJECTED", {"reason": str(exc)}
                )
            await self.book.bind_order(trade, Role.ENTRY, order_id, placed)
            await self.audit.write(
                "order_placed", self.clock.now(), data={"trade": trade.id, "mode": mode}
            )
            return trade

    async def _adopt_or_flag(
        self, trade: Trade, order_id: str, request: OrderRequest, reason: str
    ) -> Trade:
        """No clear answer from the broker. Look in the order book; never place it again."""
        try:
            for order in await self._broker(trade).orders():
                if order.request.trade_id == trade.id:
                    await self.book.bind_order(trade, Role.ENTRY, order_id, order)
                    return trade
        except BrokerError:
            pass
        await self.alerts.alert("Order state unknown", f"{trade.trading_symbol}: {reason}")
        return await self.book.save(
            _advance(trade, TradeEvent.ANOMALY), "ENTRY_UNKNOWN", {"reason": reason}
        )

    async def cancel_entry(self, trade_id: str) -> Trade:
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            return await self._cancel_entry(self._require(trade_id))

    async def _cancel_entry(self, trade: Trade) -> Trade:
        if trade.state is not TradeState.ENTRY_PENDING:
            raise TradeError("NOT_PENDING", "Only a pending entry can be cancelled")
        broker_id = self.book.broker_id(trade.entry_order_id)
        if broker_id is not None:
            try:
                await self._broker(trade).cancel_order(broker_id)
            except BrokerError as exc:
                raise TradeError("CANCEL_FAILED", str(exc)) from exc
        return trade

    # --- stop ----------------------------------------------------------------

    async def _place_stop(self, trade: Trade) -> Trade:
        """A stop goes to the exchange for the filled quantity, the moment there is a fill."""
        request = self._sell_request(
            trade,
            Intent.STOP,
            OrderType.SL,
            trade.filled_qty,
            trade.stop_limit,
            trade.stop_trigger,
        )
        order_id = await self.book.add_order(trade, Role.STOP, request)
        trade = await self.book.save(
            trade.model_copy(update={"stop_order_id": order_id}), "STOP_ORDER_CREATED"
        )
        try:
            placed = await self._broker(trade).place_order(request)
        except BrokerUnknownOutcome as exc:
            await self.alerts.alert("Stop state unknown", f"{trade.trading_symbol}: {exc}")
            return await self.book.save(
                _advance(trade, TradeEvent.ANOMALY), "STOP_UNKNOWN", {"reason": str(exc)}
            )
        except BrokerError as exc:
            await self.book.fail_order(order_id, str(exc))
            await self.alerts.alert("Stop refused", f"{trade.trading_symbol}: exiting now")
            trade = trade.model_copy(update={"stop_order_id": None})
            return await self._exit(trade, ExitReason.STOP)
        await self.book.bind_order(trade, Role.STOP, order_id, placed)
        return await self.book.save(trade, "STOP_PLACED", {"trigger": str(trade.stop_trigger)})

    async def ensure_stop(self, trade_id: str) -> None:
        """Reconcile found an open position with no live stop."""
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            trade = self._require(trade_id)
            if trade.state is TradeState.OPEN:
                await self.alerts.alert(
                    "Missing stop replaced", f"{trade.trading_symbol}: stop placed again"
                )
                await self._place_stop(trade.model_copy(update={"stop_order_id": None}))

    async def resize_stop(self, trade_id: str, qty: int) -> None:
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            stop_id = self.book.broker_id(trade.stop_order_id)
            if stop_id is not None:
                await self._broker(trade).modify_order(stop_id, OrderChanges(qty=qty))

    async def modify_stop(
        self, trade_id: str, new_trigger: Decimal, source: OrderSource = OrderSource.MANUAL
    ) -> Trade:
        """Tighten a stop. Trailing comes through here, so the guard sees every move."""
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            trade = self._require(trade_id)
            if trade.state is not TradeState.OPEN:
                raise TradeError("NOT_OPEN", "Only an open trade's stop can move")
            request = self._sell_request(
                trade,
                Intent.MODIFY_STOP,
                OrderType.SL,
                trade.filled_qty,
                trade.stop_limit,
                new_trigger,
            ).model_copy(update={"source": source})
            decision = await self.guard.evaluate(
                trade.account_id, trade.mode, request, current_stop=trade.stop_trigger
            )
            if not decision.allowed:
                raise OrderBlocked(decision)
            stop_id = self.book.broker_id(trade.stop_order_id)
            if stop_id is None:
                raise TradeError("NO_STOP", "The stop is not at the exchange yet")
            limit = stop_limit_price(new_trigger, trade.tick_size, self.config.stop_limit_pct)
            try:
                await self._broker(trade).modify_order(
                    stop_id, OrderChanges(price=limit, trigger=new_trigger)
                )
            except BrokerError as exc:
                raise TradeError("MODIFY_FAILED", str(exc)) from exc
            moved = trade.model_copy(
                update={"stop_trigger": new_trigger, "stop_limit": limit, "stop_moved": True}
            )
            return await self.book.save(
                moved, "STOP_MOVED", {"trigger": str(new_trigger), "source": source}
            )

    # --- exit ----------------------------------------------------------------

    def _exit_price(self, trade: Trade) -> Decimal:
        tick = self.quotes.get(trade.instrument_key)
        if tick is None:
            return trade.stop_limit
        price = tick.bid if tick.bid is not None else tick.ltp
        return max(floor_tick(price, trade.tick_size), trade.tick_size)

    async def _position_qty(self, trade: Trade) -> int:
        positions = await self._broker(trade).positions()
        held = sum(p.net_qty for p in positions if p.instrument_key == trade.instrument_key)
        return min(held, trade.filled_qty)

    async def exit_trade(self, trade_id: str, reason: ExitReason) -> Trade:
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            return await self._exit(self._require(trade_id), reason)

    async def _cancel_rest_of_entry(self, trade: Trade) -> None:
        entry_id = self.book.broker_id(trade.entry_order_id)
        if entry_id is None or trade.filled_qty >= trade.qty:
            return
        try:
            await self._broker(trade).cancel_order(entry_id)
        except BrokerError:
            log.info("the rest of the entry was already closed")
        self.book.timers(trade.id).partial_since = None

    async def _exit(self, trade: Trade, reason: ExitReason) -> Trade:
        """Modify the stop into a marketable limit. Never cancel the stop first."""
        if trade.state is TradeState.EXITING:
            return trade
        if trade.state is TradeState.ENTRY_PENDING and trade.filled_qty == 0:
            return await self._cancel_entry(trade)
        if trade.state not in (TradeState.OPEN, TradeState.NEEDS_ATTENTION):
            raise TradeError("NOT_OPEN", "That trade has no position to exit")

        await self._cancel_rest_of_entry(trade)
        held = await self._position_qty(trade)
        if held <= 0:
            return trade  # the stop already filled; its update will close the trade

        now = self.clock.now()
        price = self._exit_price(trade)
        trade = await self.book.save(
            _advance(trade, TradeEvent.EXIT_TRIGGER, exit_reason=reason),
            "EXIT_STARTED",
            {"reason": reason, "price": str(price)},
        )
        timers = self.book.timers(trade.id)
        timers.exit_started_at = timers.last_reprice_at = now

        stop_id = self.book.broker_id(trade.stop_order_id)
        if stop_id is not None:
            try:
                await self._broker(trade).modify_order(
                    stop_id, OrderChanges(order_type=OrderType.LIMIT, price=price, qty=held)
                )
            except BrokerError as exc:
                log.warning("modifying the stop into an exit failed: %s", exc)
            return trade

        request = self._sell_request(trade, Intent.EXIT, OrderType.LIMIT, held, price)
        order_id = await self.book.add_order(trade, Role.EXIT, request)
        trade = await self.book.save(
            trade.model_copy(update={"exit_order_id": order_id}), "EXIT_ORDER_CREATED"
        )
        try:
            placed = await self._broker(trade).place_order(request)
        except BrokerError as exc:
            await self.book.fail_order(order_id, str(exc))
            await self.alerts.alert("Exit failed", f"{trade.trading_symbol}: {exc}")
            return await self.book.save(
                _advance(trade, TradeEvent.ANOMALY), "EXIT_FAILED", {"reason": str(exc)}
            )
        await self.book.bind_order(trade, Role.EXIT, order_id, placed)
        return trade

    async def flatten(self, account_id: str, mode: Mode, reason: ExitReason) -> None:
        """Cancel pending entries and exit open trades. Safe to repeat."""
        async with self._lock(account_id):
            for trade in self.book.active(account_id, mode):
                if trade.state is TradeState.EXITING:
                    continue
                try:
                    await self._exit(trade, reason)
                except TradeError as exc:
                    log.warning("flatten: %s %s", trade.id, exc.message)

    # --- broker updates ------------------------------------------------------

    async def on_order_update(self, account_id: str, mode: Mode, update: Order) -> None:
        async with self._lock(account_id):
            link = self.book.link(mode, update.broker_order_id)
            if link is None:
                await self._external(account_id, mode, update)
                return
            await self.book.update_order(link, update)
            trade = self.book.get(link.trade_id)
            if trade is None:
                return
            if link.role is Role.ENTRY:
                await self._on_entry(trade, update)
            else:
                await self._on_exit_order(trade, link, update)

    async def _external(self, account_id: str, mode: Mode, update: Order) -> None:
        """An order placed in the broker's own app counts toward today's limits (D4)."""
        day = self.calendar.trading_date(self.clock.now())
        if await self.book.record_external(account_id, mode, day, update):
            counters = self.book.counters(account_id, mode, day)
            counters.executed_orders += 1
            if update.request.side is Side.BUY:
                counters.trades_taken += 1

    async def _on_entry(self, trade: Trade, update: Order) -> None:
        counters = self.book.counters(trade.account_id, trade.mode, trade.trading_date)
        if update.filled_qty > trade.filled_qty:
            first_fill = trade.filled_qty == 0
            changes = {"filled_qty": update.filled_qty, "entry_avg": update.avg_price}
            if first_fill:
                counters.trades_taken += 1
                counters.executed_orders += 1
                trade = await self.book.save(
                    _advance(trade, TradeEvent.FILL, opened_at=self.clock.now(), **changes),
                    "ENTRY_FILLED",
                    {"qty": update.filled_qty, "avg": str(update.avg_price)},
                )
                if trade.filled_qty < trade.qty:
                    self.book.timers(trade.id).partial_since = self.clock.now()
                await self._place_stop(trade)
            else:
                trade = await self.book.save(
                    trade.model_copy(update=changes),
                    "ENTRY_TOPPED_UP",
                    {"qty": update.filled_qty},
                )
                stop_id = self.book.broker_id(trade.stop_order_id)
                if stop_id is not None:
                    await self._broker(trade).modify_order(
                        stop_id, OrderChanges(qty=trade.filled_qty)
                    )
                if trade.filled_qty >= trade.qty:
                    self.book.timers(trade.id).partial_since = None
            return

        if trade.state is not TradeState.ENTRY_PENDING:
            return
        if update.status is OrderStatus.REJECTED:
            await self.book.save(
                _advance(trade, TradeEvent.REJECT),
                "ENTRY_REJECTED",
                {"reason": update.reject_reason},
            )
        elif update.status is OrderStatus.CANCELLED:
            await self.book.save(_advance(trade, TradeEvent.CANCEL), "ENTRY_CANCELLED")

    async def _on_exit_order(self, trade: Trade, link: OrderLink, update: Order) -> None:
        if update.status is OrderStatus.COMPLETE:
            await self._close(trade, update)
            return
        if update.status not in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
            return
        if trade.state is TradeState.OPEN:
            # The exchange or someone in the broker's app killed the stop: never stay naked.
            await self.alerts.alert("Stop lost", f"{trade.trading_symbol}: {update.reject_reason}")
            cleared = trade.model_copy(update={"stop_order_id": None})
            if update.status is OrderStatus.REJECTED:
                await self._exit(cleared, ExitReason.STOP)
            else:
                await self._place_stop(cleared)
        elif trade.state is TradeState.EXITING:
            await self.book.save(
                _advance(trade, TradeEvent.ANOMALY),
                "EXIT_ORDER_LOST",
                {"reason": update.reject_reason},
            )
            await self.alerts.alert("Exit order lost", f"{trade.trading_symbol}: check it now")

    async def _close(self, trade: Trade, fill: Order) -> None:
        now = self.clock.now()
        exit_avg = fill.avg_price or trade.stop_limit
        entry = trade.entry_avg or trade.entry_limit
        realised = (exit_avg - entry) * fill.filled_qty
        reason = trade.exit_reason
        if reason is None:
            reason = ExitReason.TRAILING if trade.stop_moved else ExitReason.STOP
        closed = await self.book.save(
            _advance(
                trade,
                TradeEvent.EXIT_FILL,
                exit_avg=exit_avg,
                realised_pnl=realised,
                closed_at=now,
                exit_reason=reason,
            ),
            "EXIT_FILLED",
            {"avg": str(exit_avg), "pnl": str(realised), "reason": reason},
        )
        counters = self.book.counters(closed.account_id, closed.mode, closed.trading_date)
        counters.executed_orders += 1
        if realised < 0:
            counters.last_loss_at = now

    async def close_from_broker(self, trade_id: str, fill: Order) -> None:
        """Reconcile found the position already gone: close with the broker's fill."""
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            await self._close(self._require(trade_id), fill)

    async def flag_attention(self, trade_id: str, reason: str) -> None:
        trade = self._require(trade_id)
        async with self._lock(trade.account_id):
            trade = self._require(trade_id)
            if trade.state is TradeState.NEEDS_ATTENTION:
                return
            await self.book.save(
                _advance(trade, TradeEvent.ANOMALY), "NEEDS_ATTENTION", {"reason": reason}
            )
            await self.alerts.alert("Needs attention", f"{trade.trading_symbol}: {reason}")

    # --- ticks and timers ----------------------------------------------------

    async def on_tick(self, key: str, ltp: Decimal) -> None:
        """Target exits, and noticing a crossed stop. Called for every tick."""
        for trade in self.book.for_instrument(key):
            if trade.state is not TradeState.OPEN or not self._reachable(trade):
                continue
            if (
                trade.target is not None
                and ltp >= trade.target
                and not trails_past_target(trade.trailing)
            ):
                await self.exit_trade(trade.id, ExitReason.TARGET)
                continue
            timers = self.book.timers(trade.id)
            if ltp > trade.stop_trigger:
                timers.stop_crossed_at = None
            elif timers.stop_crossed_at is None:
                timers.stop_crossed_at = self.clock.now()

    async def housekeeping(self) -> None:
        """The clock-driven rules: re-price, escalate, and clean up partial fills."""
        now = self.clock.now()
        for trade in self.book.all_active():
            if not self._reachable(trade):
                continue
            timers = self.book.timers(trade.id)
            if trade.state is TradeState.EXITING:
                await self._chase_exit(trade)
            elif trade.state is TradeState.OPEN:
                crossed = timers.stop_crossed_at
                if crossed is not None and now - crossed >= REPRICE_AFTER:
                    await self.exit_trade(trade.id, ExitReason.STOP)
                elif (
                    timers.partial_since is not None
                    and now - timers.partial_since >= CANCEL_REST_AFTER
                ):
                    async with self._lock(trade.account_id):
                        await self._cancel_rest_of_entry(trade)

    async def _chase_exit(self, trade: Trade) -> None:
        timers = self.book.timers(trade.id)
        moment = self.clock.now()
        started = timers.exit_started_at
        if started is None:
            timers.exit_started_at = timers.last_reprice_at = moment
            return
        if moment - started >= ATTENTION_AFTER:
            await self.flag_attention(trade.id, "The exit did not fill in 10 seconds")
            return
        last = timers.last_reprice_at or started
        if moment - last < REPRICE_AFTER:
            return
        order_id = self.book.broker_id(trade.exit_order_id or trade.stop_order_id)
        if order_id is None:
            return
        async with self._lock(trade.account_id):
            timers.last_reprice_at = moment
            try:
                await self._broker(trade).modify_order(
                    order_id,
                    OrderChanges(order_type=OrderType.LIMIT, price=self._exit_price(trade)),
                )
            except BrokerError as exc:
                log.info("re-pricing the exit failed: %s", exc)
