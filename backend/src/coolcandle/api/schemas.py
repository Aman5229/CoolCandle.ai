"""Request and response bodies. Decimals travel as strings, so no float ever touches money."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from coolcandle.domain.events import EventImpact
from coolcandle.domain.guard import DayStatus
from coolcandle.domain.models import DaySnapshot, Mode, TrailingRule
from coolcandle.domain.plan import DailyPlan


class LoginBody(BaseModel):
    email: str
    password: str
    code: str


class MeResponse(BaseModel):
    email: str
    mode: Mode
    account_id: str
    is_simulated: bool


class CredentialsBody(BaseModel):
    broker: str
    label: str = "Kotak Neo"
    credentials: dict[str, str]


class ConnectBody(BaseModel):
    totp: str


class FundsResponse(BaseModel):
    available: Decimal
    used_margin: Decimal


class PlanBody(BaseModel):
    profit_target: Decimal
    loss_limit: Decimal
    max_trades: int
    max_loss_per_trade: Decimal
    entry_start: time = time(9, 20)
    entry_cutoff: time = time(14, 30)
    square_off: time = time(15, 15)
    cool_off_minutes: int = 0
    min_reward_risk: Decimal = Decimal("1")
    allowed_underlyings: tuple[str, ...] = ("NIFTY", "SENSEX")
    trail_at_target: bool = False
    target_giveback_pct: Decimal = Decimal("20")


class ConfirmBody(BaseModel):
    step: Literal[1, 2]
    typed: str | None = None


class DayResponse(BaseModel):
    trading_date: date
    mode: Mode
    status: DayStatus
    snapshot: DaySnapshot
    plan: DailyPlan | None
    locked_at: datetime | None
    lock_ends_at: datetime | None
    market_open: bool
    broker_kill_switch_url: str = "https://neo.kotaksecurities.com/"


class ModeBody(BaseModel):
    mode: Mode
    typed: str | None = None


class TicketBody(BaseModel):
    instrument_key: str
    lots: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_trigger: Decimal = Field(gt=0)
    target: Decimal | None = None
    trailing: TrailingRule | None = None


class ModifyStopBody(BaseModel):
    trigger: Decimal = Field(gt=0)


class TakeSignalBody(BaseModel):
    lots: int | None = None


class SettingsBody(BaseModel):
    values: dict[str, object]


class EventBody(BaseModel):
    day: date
    name: str
    at: time | None = None
    impact: EventImpact = EventImpact.HIGH
    block_before_minutes: int = 15
    block_after_minutes: int = 15


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
