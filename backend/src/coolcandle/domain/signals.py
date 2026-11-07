"""Turn an index setup into an option trade. Code owns every number here."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime

from coolcandle.domain.exits import ceil_tick, floor_tick
from coolcandle.domain.models import Frozen, Instrument, JsonDict, OptionType, Tick
from coolcandle.domain.setups.base import Direction, SetupHit


class SignalStatus(StrEnum):
    NEW = "NEW"
    TAKEN = "TAKEN"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class SignalConfig:
    delta: Decimal = Decimal("0.5")  # ATM option moves about half the index
    target_r: Decimal = Decimal("2")
    valid_minutes: int = 10
    band_pct: Decimal = Decimal("3")


class Signal(Frozen):
    id: str
    setup: str
    setup_version: str
    underlying: str
    direction: Direction
    instrument_key: str
    trading_symbol: str
    lot_size: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    expires_at: AwareDatetime
    reason: str
    features: JsonDict = {}
    status: SignalStatus = SignalStatus.NEW


def option_side(direction: Direction) -> OptionType:
    return OptionType.CE if direction is Direction.BULLISH else OptionType.PE


def build_signal(
    signal_id: str, hit: SetupHit, option: Instrument, quote: Tick, config: SignalConfig
) -> Signal | None:
    """An option signal from a hit, or None when the premium risk makes no sense."""
    entry = quote.ask if quote.ask is not None else quote.ltp
    premium_risk = abs(hit.close - hit.index_stop) * config.delta
    stop = floor_tick(entry - premium_risk, option.tick_size)
    if premium_risk <= 0 or stop <= 0:
        return None
    target = ceil_tick(entry + config.target_r * (entry - stop), option.tick_size)
    return Signal(
        id=signal_id,
        setup=hit.setup,
        setup_version=hit.version,
        underlying=hit.underlying,
        direction=hit.direction,
        instrument_key=option.key,
        trading_symbol=option.trading_symbol,
        lot_size=option.lot_size,
        entry=entry,
        stop=stop,
        target=target,
        expires_at=hit.at + timedelta(minutes=config.valid_minutes),
        reason=hit.reason,
        features=hit.features,
    )


def within_band(signal: Signal, price: Decimal, band_pct: Decimal) -> bool:
    return abs(price - signal.entry) / signal.entry * 100 <= band_pct
