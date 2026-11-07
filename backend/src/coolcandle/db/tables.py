"""Postgres tables. Money is NUMERIC(14,2), prices NUMERIC(12,2), times are UTC."""

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 2)
Price = Numeric(12, 2)


class UtcDateTime(TypeDecorator[datetime]):
    """Always timezone-aware UTC, even on databases that drop the zone."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Row(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class User(Row):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Session(Row):
    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    mode: Mapped[str] = mapped_column(String(8), default="paper")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class BrokerAccount(Row):
    """One row per user that never goes away, so plans and locks outlive any credential change."""

    __tablename__ = "broker_accounts"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    broker: Mapped[str] = mapped_column(String(32), default="paper")
    label: Mapped[str] = mapped_column(String(64), default="Paper")
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="not_connected")
    last_connected_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class DailyPlanRow(Row):
    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("broker_account_id", "trading_date"),)

    broker_account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id"))
    trading_date: Mapped[date] = mapped_column(Date)
    profit_target: Mapped[Decimal] = mapped_column(Money)
    loss_limit: Mapped[Decimal] = mapped_column(Money)
    max_trades: Mapped[int] = mapped_column(Integer)
    max_loss_per_trade: Mapped[Decimal] = mapped_column(Money)
    entry_start: Mapped[time] = mapped_column(Time)
    entry_cutoff: Mapped[time] = mapped_column(Time)
    square_off: Mapped[time] = mapped_column(Time)
    cool_off_minutes: Mapped[int] = mapped_column(Integer)
    min_reward_risk: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    allowed_underlyings: Mapped[list[str]] = mapped_column(Json)
    trail_at_target: Mapped[bool] = mapped_column(Boolean, default=False)
    target_giveback_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(16))
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class PlanChange(Row):
    __tablename__ = "plan_changes"

    plan_id: Mapped[str] = mapped_column(ForeignKey("daily_plans.id"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    old: Mapped[str] = mapped_column(String(255))
    new: Mapped[str] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class DayLock(Row):
    """No code path deletes these rows."""

    __tablename__ = "day_locks"
    __table_args__ = (UniqueConstraint("broker_account_id", "trading_date", "mode"),)

    broker_account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id"))
    trading_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict[str, Any]] = mapped_column(Json)
    locked_at: Mapped[datetime] = mapped_column(UtcDateTime)


class TradeRow(Row):
    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_day", "broker_account_id", "trading_date", "mode"),)

    broker_account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id"))
    mode: Mapped[str] = mapped_column(String(8))
    trading_date: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20))
    instrument_key: Mapped[str] = mapped_column(String(64))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    exchange_segment: Mapped[str] = mapped_column(String(16))
    tick_size: Mapped[Decimal] = mapped_column(Price)
    qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    entry_limit: Mapped[Decimal] = mapped_column(Price)
    stop_trigger: Mapped[Decimal] = mapped_column(Price)
    stop_limit: Mapped[Decimal] = mapped_column(Price)
    target: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    entry_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stop_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exit_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entry_avg: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    exit_avg: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    realised_pnl: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    source: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trailing: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    stop_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    signal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class OrderRow(Row):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_broker", "broker_account_id", "mode", "broker_order_id"),)

    broker_account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id"))
    mode: Mapped[str] = mapped_column(String(8))
    trading_date: Mapped[date] = mapped_column(Date)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    role: Mapped[str | None] = mapped_column(String(8), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(16))
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external: Mapped[bool] = mapped_column(Boolean, default=False)


class TradeEventRow(Row):
    __tablename__ = "trade_events"

    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id"), index=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    type: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(Json)


class AuditLog(Row):
    __tablename__ = "audit_log"

    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    action: Mapped[str] = mapped_column(String(48))
    data: Mapped[dict[str, Any]] = mapped_column(Json)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Holiday(Row):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("date", "exchange"),)

    date: Mapped[date] = mapped_column(Date)
    exchange: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))


class MarketEventRow(Row):
    __tablename__ = "market_events"
    __table_args__ = (UniqueConstraint("day", "name"),)

    day: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(128))
    at: Mapped[time | None] = mapped_column(Time, nullable=True)
    impact: Mapped[str] = mapped_column(String(8))
    block_before_minutes: Mapped[int] = mapped_column(Integer, default=15)
    block_after_minutes: Mapped[int] = mapped_column(Integer, default=15)


class Setting(Row):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(Json)


class SignalRow(Row):
    __tablename__ = "signals"

    broker_account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id"), index=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    setup: Mapped[str] = mapped_column(String(32))
    setup_version: Mapped[str] = mapped_column(String(16))
    underlying: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    instrument_key: Mapped[str] = mapped_column(String(64))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    lot_size: Mapped[int] = mapped_column(Integer)
    entry: Mapped[Decimal] = mapped_column(Price)
    stop: Mapped[Decimal] = mapped_column(Price)
    target: Mapped[Decimal] = mapped_column(Price)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    reason: Mapped[str] = mapped_column(Text)
    features: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(12))


class Experiment(Row):
    """Every backtest run, so results stay comparable."""

    __tablename__ = "experiments"

    setup: Mapped[str] = mapped_column(String(32))
    setup_version: Mapped[str] = mapped_column(String(16))
    data_from: Mapped[date] = mapped_column(Date)
    data_to: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(Json)
