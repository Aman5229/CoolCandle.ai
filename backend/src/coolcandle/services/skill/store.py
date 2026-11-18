"""Database access for the skill_* tables. Nothing here reads or writes a live table."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import (
    SkillAccount,
    SkillEventRow,
    SkillOrder,
    SkillRun,
    SkillSession,
    SkillTradeRow,
    new_id,
)
from coolcandle.domain.models import Order
from coolcandle.domain.skill.models import (
    EventKind,
    ExitKind,
    RunStatus,
    SessionState,
    SkillEvent,
    SkillTrade,
)

FINAL_STATES = (SessionState.LOCKED, SessionState.ENDED)


def trade_status(trade: SkillTrade) -> str:
    if trade.closed_at is None:
        return "OPEN" if trade.entry is not None else "PENDING"
    return "CLOSED" if trade.entry is not None else "CANCELLED"


def trade_from_row(row: SkillTradeRow) -> SkillTrade:
    return SkillTrade(
        id=row.id,
        instrument_key=row.instrument_key,
        trading_symbol=row.trading_symbol,
        qty=row.qty,
        entry=row.entry,
        stop_at_fill=row.stop_at_fill,
        stop=row.stop,
        target=row.target,
        exit=row.exit,
        r_risk=row.r_risk,
        pnl=row.pnl,
        fees=row.fees,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        exit_kind=ExitKind(row.exit_kind) if row.exit_kind else None,
        stop_widened=row.stop_widened,
    )


def event_from_row(row: SkillEventRow) -> SkillEvent:
    return SkillEvent(
        id=row.id, ts=row.ts, kind=EventKind(row.kind), trade_id=row.trade_id, data=row.payload
    )


class SkillStore:
    def __init__(self, db: Sessions) -> None:
        self.db = db

    async def save(self, *rows: Any) -> None:
        """Insert or update. Every row is given its id up front, so callers can keep using it."""
        async with self.db() as session, session.begin():
            for row in rows:
                await session.merge(row)

    # --- accounts and runs -----------------------------------------------------------------

    async def account(self, user_id: str) -> SkillAccount:
        async with self.db() as session, session.begin():
            found = await session.scalar(
                select(SkillAccount).where(SkillAccount.user_id == user_id)
            )
            if found is None:
                found = SkillAccount(id=new_id(), user_id=user_id, settings={})
                session.add(found)
            return found

    async def account_of(self, account_id: str) -> SkillAccount | None:
        async with self.db() as session:
            return await session.get(SkillAccount, account_id)

    async def runs(self, account_id: str) -> list[SkillRun]:
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillRun)
                .where(SkillRun.account_id == account_id)
                .order_by(SkillRun.number.desc())
            )
            return list(rows)

    async def run(self, run_id: str) -> SkillRun | None:
        async with self.db() as session:
            return await session.get(SkillRun, run_id)

    async def active_run(self, account_id: str) -> SkillRun | None:
        async with self.db() as session:
            return await session.scalar(
                select(SkillRun).where(
                    SkillRun.account_id == account_id, SkillRun.status == RunStatus.ACTIVE
                )
            )

    async def add_run(
        self, account_id: str, start_fund: Decimal, today: date, now: datetime
    ) -> SkillRun:
        async with self.db() as session, session.begin():
            last = await session.scalar(
                select(func.max(SkillRun.number)).where(SkillRun.account_id == account_id)
            )
            run = SkillRun(
                id=new_id(),
                account_id=account_id,
                number=(last or 0) + 1,
                start_fund=start_fund,
                cash=start_fund,
                status=RunStatus.ACTIVE,
                today=today,
                started_at=now,
                peak_equity=start_fund,
            )
            session.add(run)
            return run

    # --- sessions --------------------------------------------------------------------------

    async def session(self, session_id: str) -> SkillSession | None:
        async with self.db() as session:
            return await session.get(SkillSession, session_id)

    async def sessions(self, run_ids: Sequence[str]) -> list[SkillSession]:
        """Newest first."""
        if not run_ids:
            return []
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillSession)
                .where(SkillSession.run_id.in_(run_ids))
                .order_by(SkillSession.created_at.desc())
            )
            return list(rows)

    async def running(self) -> list[SkillSession]:
        """Started and not yet locked or ended: what a restart has to bring back."""
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillSession).where(SkillSession.state == SessionState.ACTIVE)
            )
            return list(rows)

    async def delete_session(self, row: SkillSession) -> None:
        """Only for a draft, which has no trades, orders or events yet."""
        async with self.db() as session, session.begin():
            found = await session.get(SkillSession, row.id)
            if found is not None:
                await session.delete(found)

    # --- trades, events and orders ---------------------------------------------------------

    async def trades(self, session_id: str) -> list[SkillTrade]:
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillTradeRow)
                .where(SkillTradeRow.session_id == session_id)
                .order_by(SkillTradeRow.opened_at)
            )
            return [trade_from_row(row) for row in rows]

    async def trade_session(self, trade_id: str) -> str | None:
        async with self.db() as session:
            row = await session.get(SkillTradeRow, trade_id)
            return row.session_id if row is not None else None

    async def save_trade(self, session_id: str, trade: SkillTrade) -> None:
        await self.save(
            SkillTradeRow(
                id=trade.id,
                session_id=session_id,
                instrument_key=trade.instrument_key,
                trading_symbol=trade.trading_symbol,
                status=trade_status(trade),
                qty=trade.qty,
                entry=trade.entry,
                stop_at_fill=trade.stop_at_fill,
                stop=trade.stop,
                target=trade.target,
                exit=trade.exit,
                r_risk=trade.r_risk,
                pnl=trade.pnl,
                fees=trade.fees,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                exit_kind=trade.exit_kind,
                stop_widened=trade.stop_widened,
            )
        )

    async def events(self, session_id: str) -> list[SkillEvent]:
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillEventRow)
                .where(SkillEventRow.session_id == session_id)
                .order_by(SkillEventRow.ts, SkillEventRow.created_at)
            )
            return [event_from_row(row) for row in rows]

    async def add_event(self, session_id: str, event: SkillEvent) -> None:
        await self.save(
            SkillEventRow(
                id=event.id,
                session_id=session_id,
                ts=event.ts,
                kind=event.kind,
                trade_id=event.trade_id,
                payload=event.data,
            )
        )

    async def orders(self, session_id: str) -> list[SkillOrder]:
        async with self.db() as session:
            rows = await session.scalars(
                select(SkillOrder).where(SkillOrder.session_id == session_id)
            )
            return list(rows)

    async def save_order(self, session_id: str, role: str, order: Order) -> None:
        async with self.db() as session, session.begin():
            row = await session.scalar(
                select(SkillOrder).where(
                    SkillOrder.session_id == session_id,
                    SkillOrder.broker_order_id == order.broker_order_id,
                )
            )
            if row is None:
                row = SkillOrder(
                    id=new_id(),
                    session_id=session_id,
                    broker_order_id=order.broker_order_id,
                )
                session.add(row)
            row.trade_id = order.request.trade_id
            row.role = role
            row.request = order.request.model_dump(mode="json")
            row.status = order.status
            row.filled_qty = order.filled_qty
            row.avg_price = order.avg_price
            row.ts = order.updated_at
