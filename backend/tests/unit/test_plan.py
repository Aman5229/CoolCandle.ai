from datetime import date, time
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from coolcandle.domain.plan import (
    DailyPlan,
    PlanError,
    PlanStatus,
    cancel_confirmation,
    confirm,
    stricter_changes,
)

DAY = date(2026, 11, 11)


def plan(**changes: object) -> DailyPlan:
    values: dict[str, object] = {
        "trading_date": DAY,
        "profit_target": Decimal("5000"),
        "loss_limit": Decimal("3000"),
        "max_trades": 3,
        "max_loss_per_trade": Decimal("1500"),
    }
    return DailyPlan.model_validate(values | changes)


def edit(base: DailyPlan, **changes: object) -> DailyPlan:
    return DailyPlan.model_validate(base.model_dump() | changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"loss_limit": Decimal("0")},
        {"profit_target": Decimal("-1")},
        {"max_trades": 0},
        {"max_trades": 21},
        {"max_loss_per_trade": Decimal("3001")},
        {"entry_cutoff": time(15, 16)},
        {"square_off": time(15, 21)},
        {"entry_start": time(14, 31)},
        {"allowed_underlyings": ()},
    ],
)
def test_bad_plans_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        plan(**changes)


def test_it_takes_two_confirmations_to_lock() -> None:
    step_one = confirm(plan(), 1)
    assert step_one.status is PlanStatus.CONFIRMING
    step_two = confirm(step_one, 2, "LOCK")
    assert step_two.status is PlanStatus.LOCKED
    assert step_two.confirmations == 2


def test_step_two_needs_the_typed_word() -> None:
    with pytest.raises(PlanError) as refused:
        confirm(confirm(plan(), 1), 2, "lock")
    assert refused.value.code == "CONFIRM_WORD"


def test_steps_cannot_be_skipped() -> None:
    with pytest.raises(PlanError):
        confirm(plan(), 2, "LOCK")


def test_a_locked_plan_cannot_be_confirmed_again() -> None:
    locked = confirm(confirm(plan(), 1), 2, "LOCK")
    with pytest.raises(PlanError):
        confirm(locked, 1)


def test_cancel_goes_back_to_draft() -> None:
    assert cancel_confirmation(confirm(plan(), 1)).status is PlanStatus.DRAFT


def test_a_lower_loss_limit_is_stricter() -> None:
    base = plan()
    changes = stricter_changes(base, edit(base, loss_limit=Decimal("2000")))
    assert changes == [("loss_limit", "3000", "2000")]


@pytest.mark.parametrize(
    "changes",
    [
        {"loss_limit": Decimal("3500")},
        {"profit_target": Decimal("6000")},
        {"max_trades": 4},
        {"max_loss_per_trade": Decimal("1600")},
        {"entry_cutoff": time(14, 45)},
        {"square_off": time(15, 20)},
        {"entry_start": time(9, 15)},
        {"min_reward_risk": Decimal("0.5")},
        {"allowed_underlyings": ("NIFTY", "SENSEX", "BANKNIFTY")},
        {"trail_at_target": True},
    ],
)
def test_loosening_a_locked_plan_is_refused(changes: dict[str, object]) -> None:
    base = plan()
    with pytest.raises(PlanError) as refused:
        stricter_changes(base, edit(base, **changes))
    assert refused.value.code == "PLAN_LOCKED"


def test_cool_off_can_only_grow() -> None:
    base = plan(cool_off_minutes=10)
    with pytest.raises(PlanError):
        stricter_changes(base, edit(base, cool_off_minutes=5))
    assert stricter_changes(base, edit(base, cool_off_minutes=20))


def test_dropping_an_underlying_is_stricter() -> None:
    base = plan()
    assert stricter_changes(base, edit(base, allowed_underlyings=("NIFTY",)))


@given(
    loss=st.decimals(min_value=1, max_value=20000, places=2),
    trades=st.integers(1, 20),
)
def test_no_accepted_edit_ever_loosens_a_field(loss: Decimal, trades: int) -> None:
    base = plan(loss_limit=Decimal("10000"), max_trades=10)
    per_trade = min(base.max_loss_per_trade, loss)
    new = edit(base, loss_limit=loss, max_trades=trades, max_loss_per_trade=per_trade)
    try:
        stricter_changes(base, new)
    except PlanError:
        return
    assert new.loss_limit <= base.loss_limit
    assert new.max_trades <= base.max_trades
