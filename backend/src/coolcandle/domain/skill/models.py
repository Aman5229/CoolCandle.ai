"""Skill Test's own world: past NIFTY and SENSEX days replayed, index options priced by a model, and
fake rupees. Nothing here is shared with live trading's plan, lock or trades."""

from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from coolcandle.domain.calendar import MARKET_CLOSE, MARKET_OPEN
from coolcandle.domain.costs import CostRates
from coolcandle.domain.models import Frozen

INDICES = ("NIFTY", "SENSEX")
START_FUNDS = (Decimal("50000"), Decimal("100000"), Decimal("500000"))
REPLAY_SPEEDS = (1, 5, 20, 60)
StopSide = Literal["low", "high"]  # which side of a candle a trade's stop is on


class RunStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOWN = "BLOWN"
    RETIRED = "RETIRED"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMING = "CONFIRMING"
    STARTED = "STARTED"


class SessionState(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    COOL_OFF = "COOL_OFF"
    ENTRIES_CLOSED = "ENTRIES_CLOSED"
    LOCKED = "LOCKED"
    ENDED = "ENDED"


class SkillLock(StrEnum):
    """The live lock reasons, plus BLOWN when the run's fund falls through its floor."""

    TARGET = "TARGET"
    LOSS_LIMIT = "LOSS_LIMIT"
    MAX_TRADES = "MAX_TRADES"
    SQUARE_OFF = "SQUARE_OFF"
    BLOWN = "BLOWN"


class ExitKind(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    MANUAL = "MANUAL"
    LOCK = "LOCK"


class EventKind(StrEnum):
    """The behaviour log the discipline score is computed from."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    STOP_MOVED = "STOP_MOVED"
    BLOCKED = "BLOCKED"
    LOCKED = "LOCKED"


class SkillSettings(Frozen):
    """Skill Test's fixed numbers (docs/skill-test.md §2). Charges are the live rate card."""

    blown_pct: Decimal = Decimal("30")
    min_stop_pct: Decimal = Decimal("0.5")  # as the live guard (D14)
    revenge_minutes: int = 3
    when_live_locked: bool = False  # closed while the live day is locked (S5)
    risk_free_pct: Decimal = Decimal("6.5")
    spread_pct: Decimal = Decimal("0.5")  # the model's bid-ask, around its price
    lot_sizes: dict[str, int] = {"NIFTY": 65, "SENSEX": 20}
    strike_steps: dict[str, Decimal] = {"NIFTY": Decimal("50"), "SENSEX": Decimal("100")}
    chain_width: int = 10  # strikes each side of the money
    rates: CostRates = CostRates()


class SessionPlan(Frozen):
    """One replayed trading day's plan, the same limits and times as the live day plan."""

    profit_target: Decimal = Field(gt=0)
    loss_limit: Decimal = Field(gt=0)
    max_trades: int = Field(ge=1, le=20)
    max_loss_per_trade: Decimal = Field(gt=0)
    cool_off_minutes: int = Field(default=0, ge=0)
    min_reward_risk: Decimal = Field(default=Decimal("1"), ge=0)
    entry_start: time = time(9, 20)
    entry_cutoff: time = time(14, 30)
    square_off: time = time(15, 15)

    @model_validator(mode="after")
    def _limits_fit(self) -> Self:
        if self.max_loss_per_trade > self.loss_limit:
            raise ValueError("max loss per trade cannot be above the session's loss limit")
        if not MARKET_OPEN <= self.entry_start < self.entry_cutoff <= self.square_off:
            raise ValueError("entries open before they close, and close by the square-off")
        if self.square_off > MARKET_CLOSE:
            raise ValueError("the square-off must be before the market closes at 15:30")
        return self


class SkillTrade(Frozen):
    """A bought index option. Prices are premiums; money (`r_risk`, `pnl`, `fees`) is rupees."""

    id: str
    instrument_key: str
    trading_symbol: str
    qty: int  # units: lots times the lot size
    entry: Decimal | None = None  # once filled
    stop_at_fill: Decimal
    stop: Decimal
    target: Decimal | None = None
    exit: Decimal | None = None
    r_risk: Decimal  # rupees at risk at entry: the trade's 1R
    pnl: Decimal = Decimal("0")  # after charges, once closed
    fees: Decimal = Decimal("0")
    opened_at: AwareDatetime
    closed_at: AwareDatetime | None = None
    exit_kind: ExitKind | None = None
    stop_widened: bool = False

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class SkillEvent(Frozen):
    id: str
    ts: datetime
    kind: EventKind
    trade_id: str | None = None
    data: dict[str, Any] = {}
