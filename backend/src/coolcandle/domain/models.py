"""Core models. Money and prices are Decimal; datetimes are timezone-aware UTC."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from coolcandle.domain.trade_state import TradeState

ZERO = Decimal("0")


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Mode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class InstrumentKind(StrEnum):
    INDEX = "INDEX"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    EQUITY = "EQUITY"


class OptionType(StrEnum):
    CE = "CE"
    PE = "PE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"
    MARKET = "MARKET"


class Product(StrEnum):
    MIS = "MIS"


class OrderSource(StrEnum):
    MANUAL = "manual"
    SIGNAL = "signal"
    AGENT = "agent"
    TRAILING = "trailing"
    SYSTEM = "system"


class Intent(StrEnum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    EXIT = "EXIT"
    MODIFY_STOP = "MODIFY_STOP"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


FINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.COMPLETE, OrderStatus.REJECTED, OrderStatus.CANCELLED}
)


class ExitReason(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    MANUAL = "MANUAL"
    DAY_LOCK = "DAY_LOCK"
    SQUARE_OFF = "SQUARE_OFF"
    TRAILING = "TRAILING"


class Instrument(Frozen):
    key: str
    exchange_segment: str
    trading_symbol: str
    kind: InstrumentKind
    underlying: str
    expiry: date | None = None
    strike: Decimal | None = None
    option_type: OptionType | None = None
    lot_size: int = 1
    tick_size: Decimal = Decimal("0.05")
    freeze_qty: int | None = None

    @model_validator(mode="after")
    def _option_fields(self) -> Self:
        fields = (self.expiry, self.strike, self.option_type)
        is_option = self.kind is InstrumentKind.OPTION
        if is_option and any(f is None for f in fields):
            raise ValueError("an option needs expiry, strike and option_type")
        if not is_option and self.option_type is not None:
            raise ValueError("only options have an option_type")
        return self


class Tick(Frozen):
    instrument_key: str
    ts: AwareDatetime
    received_at: AwareDatetime
    ltp: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume_today: int | None = None
    oi: int | None = None


class Candle(Frozen):
    instrument_key: str
    interval: str
    start: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0
    oi: int | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high and low must contain open and close")
        return self


class OrderRequest(Frozen):
    instrument_key: str
    trading_symbol: str
    exchange_segment: str
    side: Side
    qty: int = Field(gt=0)
    order_type: OrderType
    price: Decimal | None = None
    trigger: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    product: Product = Product.MIS
    source: OrderSource = OrderSource.MANUAL
    intent: Intent
    trade_id: str | None = None
    confirm_token: str | None = None


class OrderChanges(Frozen):
    order_type: OrderType | None = None
    price: Decimal | None = None
    trigger: Decimal | None = None
    qty: int | None = None


class Order(Frozen):
    id: str
    broker_order_id: str
    request: OrderRequest
    status: OrderStatus
    filled_qty: int = 0
    avg_price: Decimal | None = None
    reject_reason: str | None = None
    updated_at: AwareDatetime


class Position(Frozen):
    instrument_key: str
    net_qty: int
    buy_qty: int = 0
    sell_qty: int = 0
    buy_value: Decimal = ZERO
    sell_value: Decimal = ZERO

    def pnl(self, mark: Decimal) -> Decimal:
        """Realised plus unrealised: cash so far, plus what is still held at `mark`."""
        return self.sell_value - self.buy_value + self.net_qty * mark


class Funds(Frozen):
    available: Decimal
    used_margin: Decimal
    as_of: AwareDatetime


class TrailKind(StrEnum):
    BREAKEVEN = "BREAKEVEN"
    TARGET_TRAIL = "TARGET_TRAIL"
    STEP = "STEP"
    SWING_LOW = "SWING_LOW"
    ATR = "ATR"


class TrailingRule(Frozen):
    kind: TrailKind
    trigger_points: Decimal = Decimal("10")  # BREAKEVEN: move to entry after this much profit
    trail_points: Decimal = Decimal("5")  # TARGET_TRAIL and STEP: distance behind price
    step_points: Decimal = Decimal("5")  # STEP: move once price gains this much
    lookback: int = 5  # SWING_LOW: closed 1-minute candles to look back on
    atr_multiple: Decimal = Decimal("1.5")  # ATR: distance in ATRs


class EntryTicket(Frozen):
    instrument_key: str
    lots: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_trigger: Decimal = Field(gt=0)
    target: Decimal | None = None
    source: OrderSource = OrderSource.MANUAL
    trailing: TrailingRule | None = None
    signal_id: str | None = None
    confirm_token: str | None = None


class Trade(Frozen):
    id: str
    account_id: str
    mode: Mode
    trading_date: date
    state: TradeState
    instrument_key: str
    trading_symbol: str
    exchange_segment: str
    tick_size: Decimal
    qty: int
    filled_qty: int = 0
    entry_limit: Decimal
    stop_trigger: Decimal
    stop_limit: Decimal
    target: Decimal | None = None
    entry_order_id: str | None = None
    stop_order_id: str | None = None
    exit_order_id: str | None = None
    entry_avg: Decimal | None = None
    exit_avg: Decimal | None = None
    realised_pnl: Decimal = ZERO
    source: OrderSource = OrderSource.MANUAL
    opened_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    exit_reason: ExitReason | None = None
    trailing: TrailingRule | None = None
    stop_moved: bool = False
    signal_id: str | None = None


class Reason(Frozen):
    code: str
    message: str


class Action(StrEnum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"
    LOCK = "LOCK"


class Decision(Frozen):
    action: Action
    reasons: tuple[Reason, ...] = ()
    max_qty: int | None = None

    @property
    def allowed(self) -> bool:
        return self.action in (Action.ALLOW, Action.WARN)


class DaySnapshot(Frozen):
    realised_pnl: Decimal = ZERO
    unrealised_pnl: Decimal = ZERO
    est_charges: Decimal = ZERO
    net_pnl: Decimal = ZERO
    peak_net_pnl: Decimal = ZERO
    trades_taken: int = 0
    open_trade: bool = False
    last_loss_at: AwareDatetime | None = None
    feed_age: float | None = None
    now: AwareDatetime


JsonDict = dict[str, Any]
