"""Plan lifecycle: draft, two confirmations, then locked. Paper and live keep a plan each. A
locked live plan only gets stricter; a locked paper plan takes any edit, since it guards no
real money. Every edit to a locked plan is recorded."""

from datetime import date
from typing import Any

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import DailyPlanRow, PlanChange
from coolcandle.domain.calendar import MarketCalendar
from coolcandle.domain.models import Mode
from coolcandle.domain.plan import (
    DailyPlan,
    PlanError,
    PlanStatus,
    cancel_confirmation,
    confirm,
    plan_changes,
    stricter_changes,
)
from coolcandle.ports.clock import Clock
from coolcandle.services.audit import Audit

_FIELDS = (
    "profit_target",
    "loss_limit",
    "max_trades",
    "max_loss_per_trade",
    "entry_start",
    "entry_cutoff",
    "square_off",
    "cool_off_minutes",
    "min_reward_risk",
    "trail_at_target",
    "target_giveback_pct",
    "confirmations",
)


def _to_plan(row: DailyPlanRow) -> DailyPlan:
    values = {name: getattr(row, name) for name in _FIELDS}
    return DailyPlan(
        trading_date=row.trading_date,
        allowed_underlyings=tuple(row.allowed_underlyings),
        status=PlanStatus(row.status),
        **values,
    )


def _apply(row: DailyPlanRow, plan: DailyPlan) -> None:
    for name in _FIELDS:
        setattr(row, name, getattr(plan, name))
    row.allowed_underlyings = list(plan.allowed_underlyings)
    row.status = plan.status


class PlanService:
    def __init__(self, db: Sessions, clock: Clock, calendar: MarketCalendar, audit: Audit) -> None:
        self.db = db
        self.clock = clock
        self.calendar = calendar
        self.audit = audit
        self._cache: dict[tuple[str, Mode, date], DailyPlan] = {}

    def today(self) -> date:
        return self.calendar.trading_date(self.clock.now())

    async def _row(
        self, session: Any, account_id: str, mode: Mode, day: date
    ) -> DailyPlanRow | None:
        found: DailyPlanRow | None = (
            await session.execute(
                select(DailyPlanRow).where(
                    DailyPlanRow.broker_account_id == account_id,
                    DailyPlanRow.trading_date == day,
                    DailyPlanRow.mode == mode,
                )
            )
        ).scalar_one_or_none()
        return found

    async def get(self, account_id: str, mode: Mode) -> DailyPlan | None:
        day = self.today()
        cached = self._cache.get((account_id, mode, day))
        if cached is not None:
            return cached
        async with self.db() as session:
            row = await self._row(session, account_id, mode, day)
        if row is None:
            return None
        plan = _to_plan(row)
        self._cache[(account_id, mode, day)] = plan
        return plan

    def locked_plan(self, account_id: str, mode: Mode) -> DailyPlan | None:
        """The cached plan, for the hot path. `get` fills it."""
        plan = self._cache.get((account_id, mode, self.today()))
        return plan if plan is not None and plan.status is PlanStatus.LOCKED else None

    async def save_draft(self, account_id: str, mode: Mode, values: dict[str, Any]) -> DailyPlan:
        """Write the draft, or edit a locked plan: any edit for paper, stricter only for live."""
        day = self.today()
        now = self.clock.now()
        async with self.db() as session, session.begin():
            row = await self._row(session, account_id, mode, day)
            if row is None:
                plan = DailyPlan(trading_date=day, **values)
                row = DailyPlanRow(broker_account_id=account_id, trading_date=day, mode=mode)
                _apply(row, plan)
                session.add(row)
            else:
                current = _to_plan(row)
                merged = current.model_dump() | values
                edited = DailyPlan.model_validate(merged)
                if current.status is PlanStatus.LOCKED:
                    edit = plan_changes if mode is Mode.PAPER else stricter_changes
                    changes = edit(current, edited)
                    await session.flush()
                    for field, old, new in changes:
                        session.add(
                            PlanChange(
                                plan_id=row.id, field=field, old=old, new=new, changed_at=now
                            )
                        )
                else:
                    edited = edited.model_copy(
                        update={"status": PlanStatus.DRAFT, "confirmations": 0}
                    )
                _apply(row, edited)
                plan = edited
        self._cache[(account_id, mode, day)] = plan
        await self.audit.write("plan_saved", now, data={"status": plan.status, "mode": mode})
        return plan

    async def confirm(self, account_id: str, mode: Mode, step: int, typed: str | None) -> DailyPlan:
        day = self.today()
        now = self.clock.now()
        async with self.db() as session, session.begin():
            row = await self._row(session, account_id, mode, day)
            if row is None:
                raise PlanError("NO_PLAN", "Fill in today's plan first")
            plan = confirm(_to_plan(row), step, typed)
            _apply(row, plan)
            if plan.status is PlanStatus.LOCKED:
                row.locked_at = now
        self._cache[(account_id, mode, day)] = plan
        await self.audit.write("plan_confirmed", now, data={"step": step, "mode": mode})
        return plan

    async def cancel(self, account_id: str, mode: Mode) -> DailyPlan:
        day = self.today()
        async with self.db() as session, session.begin():
            row = await self._row(session, account_id, mode, day)
            if row is None:
                raise PlanError("NO_PLAN", "There is no plan today")
            plan = cancel_confirmation(_to_plan(row))
            _apply(row, plan)
        self._cache[(account_id, mode, day)] = plan
        return plan
