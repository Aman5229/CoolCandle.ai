"""Index options for Skill Test, priced by a model, because expired contracts have no history.

Every premium is Black-Scholes on the replayed index price, with the volatility the session gives
it (India VIX at that minute, or the index's own recent volatility). It is a model price, not a
quote that ever traded: good for practising discipline, not for testing a strategy. All the maths
is Decimal; the normal distribution uses the Abramowitz-Stegun approximation (error below 2e-7).
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.models import Instrument, InstrumentKind, OptionType

ZERO = Decimal("0")
ONE = Decimal("1")
TICK = Decimal("0.05")
SEGMENT = "SKILL"
PREFIX = "skill|"
EXPIRES_AT = time(15, 30)
YEAR = Decimal(365 * 24 * 3600)
TRADING_DAYS_A_YEAR = Decimal("252")
MONDAY, TUESDAY, THURSDAY, FRIDAY = 0, 1, 3, 4

# Weekly expiry weekday, by the date it applies from. NSE moved NIFTY from Thursday to Tuesday
# on 1 Sep 2025. BSE moved SENSEX from Friday to Tuesday in January 2025 (the 3 Jan contract kept
# its Friday), then to Thursday on 1 Sep 2025.
EXPIRY_DAYS: dict[str, tuple[tuple[date, int], ...]] = {
    "NIFTY": ((date.min, THURSDAY), (date(2025, 9, 1), TUESDAY)),
    "SENSEX": ((date.min, FRIDAY), (date(2025, 1, 4), TUESDAY), (date(2025, 9, 1), THURSDAY)),
}


# --- the calendar ---------------------------------------------------------------------------


def expiry_weekday(index: str, day: date) -> int:
    weekday = MONDAY
    for since, value in EXPIRY_DAYS[index]:
        if day >= since:
            weekday = value
    return weekday


def next_expiry(index: str, day: date, is_trading_day: Callable[[date], bool]) -> date:
    """The weekly expiry on or after `day`. An expiry that falls on a holiday moves back to the
    trading day before it."""
    scheduled = day
    while scheduled.weekday() != expiry_weekday(index, scheduled):
        scheduled += timedelta(days=1)
    expiry = scheduled
    while expiry > day and not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


def strikes(spot: Decimal, step: Decimal, width: int) -> list[Decimal]:
    """The strike nearest the money, and `width` either side of it."""
    atm = (spot / step).quantize(ONE, rounding=ROUND_HALF_UP) * step
    return [atm + step * offset for offset in range(-width, width + 1)]


# --- the model ------------------------------------------------------------------------------


def _phi(x: Decimal) -> Decimal:
    """The standard normal distribution function, symmetric by construction."""
    if x < 0:
        return ONE - _phi(-x)
    z = x / Decimal(2).sqrt()
    t = ONE / (ONE + Decimal("0.3275911") * z)
    poly = t * (
        Decimal("0.254829592")
        + t
        * (
            Decimal("-0.284496736")
            + t
            * (Decimal("1.421413741") + t * (Decimal("-1.453152027") + t * Decimal("1.061405429")))
        )
    )
    erf = ONE - poly * (-(z * z)).exp()
    return (ONE + erf) / 2


def years_to_expiry(now: datetime, expiry: date) -> Decimal:
    left = ist_datetime(expiry, EXPIRES_AT) - now
    seconds = Decimal(left.days * 86400 + left.seconds)
    return max(seconds, ZERO) / YEAR


def model_price(
    kind: OptionType, spot: Decimal, strike: Decimal, years: Decimal, vol: Decimal, rate: Decimal
) -> Decimal:
    """Black-Scholes, unrounded. `vol` and `rate` are fractions (0.14 for 14%)."""
    discount = (-rate * years).exp()
    spread = vol * years.sqrt()
    if spread <= 0:
        value = spot - strike * discount if kind is OptionType.CE else strike * discount - spot
        return max(value, ZERO)
    d1 = ((spot / strike).ln() + (rate + vol * vol / 2) * years) / spread
    d2 = d1 - spread
    if kind is OptionType.CE:
        return spot * _phi(d1) - strike * discount * _phi(d2)
    return strike * discount * _phi(-d2) - spot * _phi(-d1)


def premium(price: Decimal, tick: Decimal = TICK) -> Decimal:
    """A model price on the tick grid, never below one tick: an option always has a bid."""
    return max((price / tick).quantize(ONE, rounding=ROUND_HALF_UP) * tick, tick)


def quote(price: Decimal, spread_pct: Decimal, tick: Decimal = TICK) -> tuple[Decimal, Decimal]:
    """(bid, ask) around a model price: `spread_pct` wide in all, and at least a tick each side."""
    half = max(price * spread_pct / 200, tick)
    bid = ((price - half) / tick).quantize(ONE, rounding=ROUND_FLOOR) * tick
    ask = ((price + half) / tick).quantize(ONE, rounding=ROUND_CEILING) * tick
    return max(bid, tick), max(ask, tick * 2)


def realised_vol(closes: Sequence[Decimal]) -> Decimal | None:
    """Annualised volatility of daily closes, for days without India VIX."""
    if len(closes) < 3:
        return None
    returns = [(b / a).ln() for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(returns, ZERO) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
    return (variance * TRADING_DAYS_A_YEAR).sqrt()


# --- contracts ------------------------------------------------------------------------------


def option_key(index: str, expiry: date, strike: Decimal, kind: OptionType) -> str:
    return f"{PREFIX}{index}|{expiry.isoformat()}|{strike.normalize():f}|{kind}"


def index_key(index: str) -> str:
    return f"{PREFIX}{index}"


def parse_key(key: str) -> tuple[str, date, Decimal, OptionType]:
    index, expiry, strike, kind = key.removeprefix(PREFIX).split("|")
    return index, date.fromisoformat(expiry), Decimal(strike), OptionType(kind)


def option_instrument(
    index: str, expiry: date, strike: Decimal, kind: OptionType, lot_size: int
) -> Instrument:
    return Instrument(
        key=option_key(index, expiry, strike, kind),
        exchange_segment=SEGMENT,
        trading_symbol=f"{index} {expiry:%d %b} {strike.normalize():f} {kind}".upper(),
        kind=InstrumentKind.OPTION,
        underlying=index,
        expiry=expiry,
        strike=strike,
        option_type=kind,
        lot_size=lot_size,
        tick_size=TICK,
    )


def index_instrument(index: str) -> Instrument:
    return Instrument(
        key=index_key(index),
        exchange_segment=SEGMENT,
        trading_symbol=index,
        kind=InstrumentKind.INDEX,
        underlying=index,
        tick_size=TICK,
    )
