"""Day summaries and trade timelines."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import DayLock, TradeEventRow, TradeRow
from coolcandle.domain.models import ZERO, Frozen, Mode, Trade
from coolcandle.services.orders.book import to_trade


class DaySummary(Frozen):
    day: date
    trades: int
    wins: int
    gross_pnl: Decimal
    lock_reason: str | None


class TimelineEvent(Frozen):
    ts: datetime
    type: str
    data: dict[str, Any]


class Journal:
    def __init__(self, db: Sessions) -> None:
        self.db = db

    async def trades(self, account_id: str, mode: Mode, day: date) -> list[Trade]:
        async with self.db() as session:
            rows = await session.execute(
                select(TradeRow)
                .where(
                    TradeRow.broker_account_id == account_id,
                    TradeRow.mode == mode,
                    TradeRow.trading_date == day,
                )
                .order_by(TradeRow.created_at)
            )
        return [to_trade(row) for row in rows.scalars()]

    async def trade(self, account_id: str, trade_id: str) -> Trade | None:
        async with self.db() as session:
            row = await session.get(TradeRow, trade_id)
        if row is None or row.broker_account_id != account_id:
            return None
        return to_trade(row)

    async def timeline(self, trade_id: str) -> list[TimelineEvent]:
        async with self.db() as session:
            rows = await session.execute(
                select(TradeEventRow)
                .where(TradeEventRow.trade_id == trade_id)
                .order_by(TradeEventRow.ts)
            )
        return [TimelineEvent(ts=r.ts, type=r.type, data=r.data) for r in rows.scalars()]

    async def days(self, account_id: str, mode: Mode, start: date, end: date) -> list[DaySummary]:
        async with self.db() as session:
            trades = await session.execute(
                select(TradeRow).where(
                    TradeRow.broker_account_id == account_id,
                    TradeRow.mode == mode,
                    TradeRow.trading_date >= start,
                    TradeRow.trading_date <= end,
                    TradeRow.filled_qty > 0,
                )
            )
            locks = await session.execute(
                select(DayLock).where(
                    DayLock.broker_account_id == account_id,
                    DayLock.mode == mode,
                    DayLock.trading_date >= start,
                    DayLock.trading_date <= end,
                )
            )
        by_day: dict[date, list[TradeRow]] = defaultdict(list)
        for row in trades.scalars():
            by_day[row.trading_date].append(row)
        reasons = {lock.trading_date: lock.reason for lock in locks.scalars()}
        days = sorted(set(by_day) | set(reasons))
        return [
            DaySummary(
                day=day,
                trades=len(by_day[day]),
                wins=sum(1 for t in by_day[day] if t.realised_pnl > 0),
                gross_pnl=sum((t.realised_pnl for t in by_day[day]), ZERO),
                lock_reason=reasons.get(day),
            )
            for day in days
        ]
