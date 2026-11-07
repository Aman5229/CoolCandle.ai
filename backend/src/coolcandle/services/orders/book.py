"""Active trades in memory, with every change written to the database first."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import OrderRow, TradeEventRow, TradeRow, new_id
from coolcandle.domain.models import (
    ZERO,
    ExitReason,
    Mode,
    Order,
    OrderRequest,
    OrderSource,
    OrderStatus,
    Side,
    Trade,
    TrailingRule,
)
from coolcandle.domain.trade_state import ACTIVE_STATES, TradeState
from coolcandle.ports.clock import Clock
from coolcandle.services.bus import EventBus


class Role(StrEnum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    EXIT = "EXIT"


@dataclass(frozen=True)
class OrderLink:
    trade_id: str
    role: Role
    order_id: str


@dataclass
class Timers:
    exit_started_at: datetime | None = None
    last_reprice_at: datetime | None = None
    stop_crossed_at: datetime | None = None
    partial_since: datetime | None = None
    last_trail_at: datetime | None = None


@dataclass
class DayCounters:
    trades_taken: int = 0
    executed_orders: int = 0
    live_orders: int = 0
    last_loss_at: datetime | None = None
    peak_net: Decimal = ZERO
    external_seen: set[str] = field(default_factory=set)


def to_trade(row: TradeRow) -> Trade:
    return Trade(
        id=row.id,
        account_id=row.broker_account_id,
        mode=Mode(row.mode),
        trading_date=row.trading_date,
        state=TradeState(row.state),
        instrument_key=row.instrument_key,
        trading_symbol=row.trading_symbol,
        exchange_segment=row.exchange_segment,
        tick_size=row.tick_size,
        qty=row.qty,
        filled_qty=row.filled_qty,
        entry_limit=row.entry_limit,
        stop_trigger=row.stop_trigger,
        stop_limit=row.stop_limit,
        target=row.target,
        entry_order_id=row.entry_order_id,
        stop_order_id=row.stop_order_id,
        exit_order_id=row.exit_order_id,
        entry_avg=row.entry_avg,
        exit_avg=row.exit_avg,
        realised_pnl=row.realised_pnl,
        source=OrderSource(row.source),
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        exit_reason=ExitReason(row.exit_reason) if row.exit_reason else None,
        trailing=TrailingRule.model_validate(row.trailing) if row.trailing else None,
        stop_moved=row.stop_moved,
        signal_id=row.signal_id,
    )


def _trade_row(trade: Trade) -> TradeRow:
    return TradeRow(
        id=trade.id,
        broker_account_id=trade.account_id,
        mode=trade.mode,
        trading_date=trade.trading_date,
        state=trade.state,
        instrument_key=trade.instrument_key,
        trading_symbol=trade.trading_symbol,
        exchange_segment=trade.exchange_segment,
        tick_size=trade.tick_size,
        qty=trade.qty,
        filled_qty=trade.filled_qty,
        entry_limit=trade.entry_limit,
        stop_trigger=trade.stop_trigger,
        stop_limit=trade.stop_limit,
        target=trade.target,
        entry_order_id=trade.entry_order_id,
        stop_order_id=trade.stop_order_id,
        exit_order_id=trade.exit_order_id,
        entry_avg=trade.entry_avg,
        exit_avg=trade.exit_avg,
        realised_pnl=trade.realised_pnl,
        source=trade.source,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        exit_reason=trade.exit_reason,
        trailing=trade.trailing.model_dump(mode="json") if trade.trailing else None,
        stop_moved=trade.stop_moved,
        signal_id=trade.signal_id,
    )


class TradeBook:
    def __init__(self, db: Sessions, clock: Clock, bus: EventBus) -> None:
        self.db = db
        self.clock = clock
        self.bus = bus
        self._active: dict[str, Trade] = {}
        self._links: dict[tuple[Mode, str], OrderLink] = {}
        self._order_ids: dict[str, str] = {}  # our order id -> broker order id
        self._timers: dict[str, Timers] = defaultdict(Timers)
        self._counters: dict[tuple[str, Mode, date], DayCounters] = defaultdict(DayCounters)

    # --- reads ---------------------------------------------------------------

    def get(self, trade_id: str) -> Trade | None:
        return self._active.get(trade_id)

    def active(self, account_id: str, mode: Mode) -> list[Trade]:
        return [t for t in self._active.values() if t.account_id == account_id and t.mode is mode]

    def for_instrument(self, key: str) -> list[Trade]:
        return [t for t in self._active.values() if t.instrument_key == key]

    def all_active(self) -> list[Trade]:
        return list(self._active.values())

    def link(self, mode: Mode, broker_order_id: str) -> OrderLink | None:
        return self._links.get((mode, broker_order_id))

    def broker_id(self, order_id: str | None) -> str | None:
        return None if order_id is None else self._order_ids.get(order_id)

    def timers(self, trade_id: str) -> Timers:
        return self._timers[trade_id]

    def counters(self, account_id: str, mode: Mode, day: date) -> DayCounters:
        return self._counters[(account_id, mode, day)]

    # --- writes --------------------------------------------------------------

    async def save(self, trade: Trade, event: str, data: dict[str, Any] | None = None) -> Trade:
        now = self.clock.now()
        async with self.db() as session, session.begin():
            await session.merge(_trade_row(trade))
            await session.flush()
            session.add(TradeEventRow(trade_id=trade.id, ts=now, type=event, data=data or {}))
        if trade.state in ACTIVE_STATES:
            self._active[trade.id] = trade
        else:
            self._active.pop(trade.id, None)
            self._timers.pop(trade.id, None)
        self.bus.publish("trade_update", trade.model_dump(mode="json"))
        return trade

    async def add_order(
        self, trade: Trade, role: Role, request: OrderRequest, order_id: str | None = None
    ) -> str:
        """Record the order before it goes to the broker, so a crash cannot lose it."""
        order_id = order_id or new_id()
        async with self.db() as session, session.begin():
            session.add(
                OrderRow(
                    id=order_id,
                    broker_account_id=trade.account_id,
                    mode=trade.mode,
                    trading_date=trade.trading_date,
                    trade_id=trade.id,
                    role=role,
                    request=request.model_dump(mode="json"),
                    status=OrderStatus.PENDING,
                )
            )
        return order_id

    async def bind_order(self, trade: Trade, role: Role, order_id: str, placed: Order) -> None:
        """Remember the broker's id for an order we just placed."""
        async with self.db() as session, session.begin():
            row = await session.get(OrderRow, order_id)
            if row is not None:
                row.broker_order_id = placed.broker_order_id
                row.status = placed.status
        self._links[(trade.mode, placed.broker_order_id)] = OrderLink(trade.id, role, order_id)
        self._order_ids[order_id] = placed.broker_order_id

    async def update_order(self, link: OrderLink, update: Order) -> None:
        async with self.db() as session, session.begin():
            row = await session.get(OrderRow, link.order_id)
            if row is not None:
                row.request = update.request.model_dump(mode="json")
                row.status = update.status
                row.filled_qty = update.filled_qty
                row.avg_price = update.avg_price
                row.reject_reason = update.reject_reason
        self.bus.publish("order_update", update.model_dump(mode="json"))

    async def fail_order(self, order_id: str, reason: str) -> None:
        async with self.db() as session, session.begin():
            row = await session.get(OrderRow, order_id)
            if row is not None:
                row.status = OrderStatus.REJECTED
                row.reject_reason = reason

    async def record_external(self, account_id: str, mode: Mode, day: date, order: Order) -> bool:
        """Store an order placed outside CoolCandle. True the first time its fill is seen."""
        counters = self.counters(account_id, mode, day)
        if order.filled_qty == 0 or order.broker_order_id in counters.external_seen:
            return False
        counters.external_seen.add(order.broker_order_id)
        async with self.db() as session, session.begin():
            session.add(
                OrderRow(
                    broker_account_id=account_id,
                    mode=mode,
                    trading_date=day,
                    broker_order_id=order.broker_order_id,
                    request=order.request.model_dump(mode="json"),
                    status=order.status,
                    filled_qty=order.filled_qty,
                    avg_price=order.avg_price,
                    external=True,
                )
            )
        return True

    # --- restart -------------------------------------------------------------

    async def load(self, account_id: str, mode: Mode, day: date) -> None:
        """Rebuild memory from the database after a restart."""
        async with self.db() as session:
            trades = (
                (
                    await session.execute(
                        select(TradeRow).where(
                            TradeRow.broker_account_id == account_id,
                            TradeRow.mode == mode,
                            TradeRow.trading_date == day,
                        )
                    )
                )
                .scalars()
                .all()
            )
            orders = (
                (
                    await session.execute(
                        select(OrderRow).where(
                            OrderRow.broker_account_id == account_id,
                            OrderRow.mode == mode,
                            OrderRow.trading_date == day,
                        )
                    )
                )
                .scalars()
                .all()
            )
        counters = DayCounters()
        for row in trades:
            trade = to_trade(row)
            if trade.state in ACTIVE_STATES:
                self._active[trade.id] = trade
            if trade.filled_qty > 0:
                counters.trades_taken += 1
            if trade.realised_pnl < 0 and trade.closed_at is not None:
                latest = counters.last_loss_at
                if latest is None or trade.closed_at > latest:
                    counters.last_loss_at = trade.closed_at
        for order in orders:
            if order.broker_order_id is None:
                continue
            if order.status != OrderStatus.REJECTED:
                counters.live_orders += 1
            if order.filled_qty > 0:
                counters.executed_orders += 1
            if order.external:
                counters.external_seen.add(order.broker_order_id)
                if order.request.get("side") == Side.BUY:
                    counters.trades_taken += 1
            elif order.trade_id is not None and order.role is not None:
                self._links[(mode, order.broker_order_id)] = OrderLink(
                    order.trade_id, Role(order.role), order.id
                )
                self._order_ids[order.id] = order.broker_order_id
        self._counters[(account_id, mode, day)] = counters

    async def day_orders(self, account_id: str, mode: Mode, day: date) -> list[OrderRow]:
        async with self.db() as session:
            rows = await session.execute(
                select(OrderRow).where(
                    OrderRow.broker_account_id == account_id,
                    OrderRow.mode == mode,
                    OrderRow.trading_date == day,
                )
            )
        return list(rows.scalars())
