"""Paper and live keep a plan each. A locked paper plan takes any edit, because it guards no real
money. A locked live plan only gets stricter, and nothing done to the paper plan reaches it."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from coolcandle.db.tables import PlanChange
from coolcandle.domain.guard import DayState, LockReason
from coolcandle.domain.models import ExitReason, Mode
from coolcandle.domain.plan import PlanError, PlanStatus
from tests.conftest import Harness, plan_values


async def test_a_locked_live_plan_only_gets_stricter(harness: Harness) -> None:
    harness.mode = Mode.LIVE
    await harness.lock_plan()
    with pytest.raises(PlanError) as refused:
        await harness.app.plans.save_draft(
            harness.account_id, Mode.LIVE, plan_values(loss_limit=Decimal("4000"))
        )
    assert refused.value.code == "PLAN_LOCKED"
    stricter = await harness.app.plans.save_draft(
        harness.account_id, Mode.LIVE, plan_values(loss_limit=Decimal("2000"))
    )
    assert (stricter.status, stricter.loss_limit) == (PlanStatus.LOCKED, Decimal("2000"))


async def test_a_locked_paper_plan_takes_any_edit_and_stays_locked(harness: Harness) -> None:
    await harness.lock_plan()
    looser = await harness.app.plans.save_draft(
        harness.account_id, Mode.PAPER, plan_values(loss_limit=Decimal("4000"), max_trades=5)
    )
    assert looser.status is PlanStatus.LOCKED
    assert (looser.loss_limit, looser.max_trades) == (Decimal("4000"), 5)
    async with harness.app.db() as session:
        logged = {row.field for row in (await session.execute(select(PlanChange))).scalars()}
    assert {"loss_limit", "max_trades"} <= logged  # every edit to a locked plan is on record


async def test_paper_and_live_plans_are_separate(harness: Harness) -> None:
    await harness.lock_plan()
    assert await harness.app.plans.get(harness.account_id, Mode.LIVE) is None
    live = await harness.app.guard.view(harness.account_id, Mode.LIVE)
    assert live.status.state is DayState.NO_PLAN


async def test_loosening_the_paper_plan_never_loosens_live(harness: Harness) -> None:
    harness.mode = Mode.LIVE
    await harness.lock_plan()
    harness.mode = Mode.PAPER
    await harness.lock_plan()
    await harness.app.plans.save_draft(
        harness.account_id, Mode.PAPER, plan_values(loss_limit=Decimal("9000"), max_trades=10)
    )
    harness.app.plans._cache.clear()  # read live back from the database, not from memory
    live = await harness.app.plans.get(harness.account_id, Mode.LIVE)
    assert live is not None
    assert (live.loss_limit, live.max_trades) == (Decimal("3000"), 3)


async def test_a_locked_paper_day_stays_locked_after_its_plan_is_loosened(
    harness: Harness,
) -> None:
    await harness.lock_plan(max_trades=1)
    trade = await harness.enter()
    await harness.app.orders.exit_trade(trade.id, ExitReason.MANUAL)
    await harness.settle()
    assert harness.app.guard.lock_reason(harness.account_id, Mode.PAPER) is LockReason.MAX_TRADES

    await harness.app.plans.save_draft(harness.account_id, Mode.PAPER, plan_values(max_trades=5))
    await harness.settle()
    view = await harness.app.guard.view(harness.account_id, Mode.PAPER)
    assert view.status.state is DayState.LOCKED
