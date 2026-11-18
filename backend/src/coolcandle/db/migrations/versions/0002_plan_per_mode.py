"""Paper and live get a plan each per day.

Until now both modes read one plan. A plan that already exists becomes the live plan, and a copy
of it becomes the paper plan, so nothing changes on the day this runs.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "daily_plans_broker_account_id_trading_date_key"  # Postgres's name for 0001's constraint
NEW = "uq_daily_plans_account_day_mode"
COPIED = (
    "created_at, updated_at, broker_account_id, trading_date, profit_target, loss_limit, "
    "max_trades, max_loss_per_trade, entry_start, entry_cutoff, square_off, cool_off_minutes, "
    "min_reward_risk, allowed_underlyings, trail_at_target, target_giveback_pct, status, "
    "confirmations, locked_at"
)


def upgrade() -> None:
    op.add_column("daily_plans", sa.Column("mode", sa.String(length=8), nullable=True))
    op.execute("UPDATE daily_plans SET mode = 'live'")
    op.alter_column("daily_plans", "mode", nullable=False)
    # The one-plan-per-day rule goes before the paper copies arrive, and the per-mode rule after.
    op.drop_constraint(OLD, "daily_plans", type_="unique")
    op.execute(
        f"INSERT INTO daily_plans (id, mode, {COPIED}) "
        f"SELECT gen_random_uuid()::text, 'paper', {COPIED} FROM daily_plans WHERE mode = 'live'"
    )
    op.create_unique_constraint(NEW, "daily_plans", ["broker_account_id", "trading_date", "mode"])


def downgrade() -> None:
    # Back to one plan per day: the live plan stays, the paper copies and their edit history go.
    op.execute(
        "DELETE FROM plan_changes WHERE plan_id IN (SELECT id FROM daily_plans WHERE mode = 'paper')"
    )
    op.execute("DELETE FROM daily_plans WHERE mode = 'paper'")
    op.drop_constraint(NEW, "daily_plans", type_="unique")
    op.drop_column("daily_plans", "mode")
    op.create_unique_constraint(OLD, "daily_plans", ["broker_account_id", "trading_date"])
