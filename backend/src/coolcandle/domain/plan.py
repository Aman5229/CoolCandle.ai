"""The daily plan: validation, the two confirmations, and stricter-only edits."""

from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from coolcandle.domain.models import Frozen

LATEST_SQUARE_OFF = time(15, 20)
CONFIRM_WORD = "LOCK"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMING = "CONFIRMING"
    LOCKED = "LOCKED"


class PlanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DailyPlan(Frozen):
    trading_date: date
    profit_target: Decimal = Field(gt=0)
    loss_limit: Decimal = Field(gt=0)
    max_trades: int = Field(ge=1, le=20)
    max_loss_per_trade: Decimal = Field(gt=0)
    entry_start: time = time(9, 20)
    entry_cutoff: time = time(14, 30)
    square_off: time = time(15, 15)
    cool_off_minutes: int = Field(default=0, ge=0)
    min_reward_risk: Decimal = Field(default=Decimal("1"), ge=0)
    allowed_underlyings: tuple[str, ...] = ("NIFTY", "SENSEX")
    trail_at_target: bool = False
    target_giveback_pct: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    status: PlanStatus = PlanStatus.DRAFT
    confirmations: int = 0

    @model_validator(mode="after")
    def _limits_fit(self) -> Self:
        if self.max_loss_per_trade > self.loss_limit:
            raise ValueError("max loss per trade cannot be above the day's loss limit")
        if not self.entry_start < self.entry_cutoff < self.square_off <= LATEST_SQUARE_OFF:
            raise ValueError(
                "times must run entry start, entry cutoff, square-off, and end by 15:20"
            )
        if not self.allowed_underlyings:
            raise ValueError("allow at least one underlying")
        return self


def confirm(plan: DailyPlan, step: int, typed: str | None = None) -> DailyPlan:
    """DRAFT -> CONFIRMING on step 1, CONFIRMING -> LOCKED on step 2 with the typed word."""
    if step == 1 and plan.status is PlanStatus.DRAFT:
        return plan.model_copy(update={"status": PlanStatus.CONFIRMING, "confirmations": 1})
    if step == 2 and plan.status is PlanStatus.CONFIRMING:
        if typed != CONFIRM_WORD:
            raise PlanError("CONFIRM_WORD", f"Type {CONFIRM_WORD} to lock the plan")
        return plan.model_copy(update={"status": PlanStatus.LOCKED, "confirmations": 2})
    raise PlanError("CONFIRM_STEP", f"Step {step} is not valid while the plan is {plan.status}")


def cancel_confirmation(plan: DailyPlan) -> DailyPlan:
    if plan.status is not PlanStatus.CONFIRMING:
        raise PlanError("CONFIRM_STEP", "There is no confirmation to cancel")
    return plan.model_copy(update={"status": PlanStatus.DRAFT, "confirmations": 0})


# True when `new` is at least as strict as `old`, per field.
_LOWER_IS_STRICTER = (
    "profit_target",
    "loss_limit",
    "max_trades",
    "max_loss_per_trade",
    "entry_cutoff",
    "square_off",
    "target_giveback_pct",
)
_HIGHER_IS_STRICTER = ("entry_start", "cool_off_minutes", "min_reward_risk")


def stricter_changes(old: DailyPlan, new: DailyPlan) -> list[tuple[str, str, str]]:
    """The (field, old, new) edits to a locked plan. Anything looser is refused."""
    changes: list[tuple[str, str, str]] = []
    for field in (*_LOWER_IS_STRICTER, *_HIGHER_IS_STRICTER):
        before, after = getattr(old, field), getattr(new, field)
        if before == after:
            continue
        looser = after > before if field in _LOWER_IS_STRICTER else after < before
        if looser:
            raise PlanError("PLAN_LOCKED", "The plan is locked for today")
        changes.append((field, str(before), str(after)))

    if not set(new.allowed_underlyings) <= set(old.allowed_underlyings):
        raise PlanError("PLAN_LOCKED", "The plan is locked for today")
    if set(new.allowed_underlyings) != set(old.allowed_underlyings):
        changes.append(
            (
                "allowed_underlyings",
                ",".join(old.allowed_underlyings),
                ",".join(new.allowed_underlyings),
            )
        )
    if new.trail_at_target and not old.trail_at_target:
        raise PlanError("PLAN_LOCKED", "The plan is locked for today")
    if old.trail_at_target and not new.trail_at_target:
        changes.append(("trail_at_target", "True", "False"))
    return changes
