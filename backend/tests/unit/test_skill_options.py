"""The option model and the replay's calendar: expiries, strikes, Black-Scholes in Decimal,
quotes, and chart timeframes folded from 1-minute candles."""

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coolcandle.domain.calendar import ist_datetime, to_ist
from coolcandle.domain.models import Candle, OptionType
from coolcandle.domain.skill.charts import fold
from coolcandle.domain.skill.options import (
    model_price,
    next_expiry,
    option_instrument,
    option_key,
    parse_key,
    premium,
    quote,
    realised_vol,
    strikes,
    years_to_expiry,
)

CE, PE = OptionType.CE, OptionType.PE
EVERY_DAY = lambda _: True  # noqa: E731


# --- expiries -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "day", "expiry"),
    [
        ("NIFTY", date(2024, 11, 11), date(2024, 11, 14)),  # Thursday
        ("NIFTY", date(2025, 8, 28), date(2025, 8, 28)),  # expiry day itself
        ("NIFTY", date(2025, 8, 29), date(2025, 9, 2)),  # then Tuesday from September 2025
        ("NIFTY", date(2025, 11, 10), date(2025, 11, 11)),
        ("SENSEX", date(2024, 12, 30), date(2025, 1, 3)),  # the last Friday expiry
        ("SENSEX", date(2025, 1, 6), date(2025, 1, 7)),  # Tuesday in 2025
        ("SENSEX", date(2025, 9, 1), date(2025, 9, 4)),  # Thursday from September 2025
    ],
)
def test_the_weekly_expiry_follows_each_exchange_through_its_changes(
    index: str, day: date, expiry: date
) -> None:
    assert next_expiry(index, day, EVERY_DAY) == expiry


def test_an_expiry_on_a_holiday_moves_to_the_trading_day_before() -> None:
    holiday = date(2025, 11, 11)
    assert next_expiry("NIFTY", date(2025, 11, 10), lambda d: d != holiday) == date(2025, 11, 10)


def test_strikes_sit_either_side_of_the_money_on_the_index_grid() -> None:
    assert strikes(Decimal("25012.4"), Decimal("50"), 2) == [
        Decimal("24900"),
        Decimal("24950"),
        Decimal("25000"),
        Decimal("25050"),
        Decimal("25100"),
    ]
    assert strikes(Decimal("81960"), Decimal("100"), 0) == [Decimal("82000")]


# --- the model ------------------------------------------------------------------------------


def test_black_scholes_matches_the_textbook_value() -> None:
    call = model_price(CE, Decimal(100), Decimal(100), Decimal(1), Decimal("0.2"), Decimal("0.05"))
    put = model_price(PE, Decimal(100), Decimal(100), Decimal(1), Decimal("0.2"), Decimal("0.05"))
    assert abs(call - Decimal("10.4506")) < Decimal("0.001")
    assert abs(put - Decimal("5.5735")) < Decimal("0.001")


def test_at_expiry_an_option_is_worth_what_it_is_in_the_money() -> None:
    zero = Decimal(0)
    assert model_price(CE, Decimal(25100), Decimal(25000), zero, Decimal("0.15"), zero) == 100
    assert model_price(PE, Decimal(25100), Decimal(25000), zero, Decimal("0.15"), zero) == 0


spots = st.integers(20000, 30000).map(Decimal)
strike_offsets = st.integers(-1500, 1500).map(Decimal)
years = st.integers(1, 90 * 24 * 60).map(lambda minutes: Decimal(minutes) / (365 * 24 * 60))
vols = st.integers(5, 60).map(lambda pct: Decimal(pct) / 100)
RATE = Decimal("0.065")


@given(spots, strike_offsets, years, vols)
def test_put_call_parity_holds(spot: Decimal, offset: Decimal, t: Decimal, vol: Decimal) -> None:
    strike = spot + offset
    call = model_price(CE, spot, strike, t, vol, RATE)
    put = model_price(PE, spot, strike, t, vol, RATE)
    assert abs(call - put - (spot - strike * (-RATE * t).exp())) < Decimal("0.000001")


@given(spots, strike_offsets, years, vols)
def test_prices_stay_inside_their_bounds(
    spot: Decimal, offset: Decimal, t: Decimal, vol: Decimal
) -> None:
    strike = spot + offset
    call = model_price(CE, spot, strike, t, vol, RATE)
    assert max(spot - strike * (-RATE * t).exp(), Decimal(0)) - Decimal("0.01") <= call <= spot
    assert model_price(PE, spot, strike, t, vol, RATE) >= -Decimal("0.01")


@given(spots, strike_offsets, years, vols)
def test_a_call_gains_and_a_put_loses_as_the_index_rises(
    spot: Decimal, offset: Decimal, t: Decimal, vol: Decimal
) -> None:
    strike = spot + offset
    up = spot + 50
    tolerance = Decimal("0.0001")
    assert (
        model_price(CE, up, strike, t, vol, RATE)
        >= model_price(CE, spot, strike, t, vol, RATE) - tolerance
    )
    assert (
        model_price(PE, up, strike, t, vol, RATE)
        <= model_price(PE, spot, strike, t, vol, RATE) + tolerance
    )


@given(spots, strike_offsets, years, vols)
def test_more_volatility_never_makes_an_option_cheaper(
    spot: Decimal, offset: Decimal, t: Decimal, vol: Decimal
) -> None:
    strike = spot + offset
    for kind in (CE, PE):
        calm = model_price(kind, spot, strike, t, vol, RATE)
        wild = model_price(kind, spot, strike, t, vol + Decimal("0.05"), RATE)
        assert wild >= calm - Decimal("0.0001")


def test_time_runs_out_at_half_past_three_on_expiry_day() -> None:
    expiry = date(2025, 11, 11)
    assert years_to_expiry(ist_datetime(expiry, time(15, 30)), expiry) == 0
    assert years_to_expiry(ist_datetime(expiry, time(16, 0)), expiry) == 0
    day_before = years_to_expiry(ist_datetime(expiry - timedelta(days=1), time(15, 30)), expiry)
    assert day_before == Decimal(1) / 365


def test_premiums_sit_on_the_tick_grid_and_quotes_straddle_them() -> None:
    assert premium(Decimal("12.3456")) == Decimal("12.35")
    assert premium(Decimal("0.001")) == Decimal("0.05")
    assert quote(Decimal("100"), Decimal("0.5")) == (Decimal("99.75"), Decimal("100.25"))
    assert quote(Decimal("1"), Decimal("0.5")) == (Decimal("0.95"), Decimal("1.05"))
    assert quote(Decimal("0.05"), Decimal("0.5")) == (Decimal("0.05"), Decimal("0.10"))


def test_realised_volatility_of_daily_closes() -> None:
    assert realised_vol([Decimal(100), Decimal(101)]) is None
    steady = realised_vol([Decimal(100) * Decimal("1.01") ** n for n in range(10)])
    assert steady is not None and steady < Decimal("0.000001")
    swinging = realised_vol([Decimal(v) for v in (100, 102, 100, 102, 100, 102)])
    assert swinging is not None and Decimal("0.3") < swinging < Decimal("0.4")


def test_an_option_key_reads_back_and_names_the_contract() -> None:
    key = option_key("NIFTY", date(2025, 11, 11), Decimal("25500"), CE)
    assert key == "skill|NIFTY|2025-11-11|25500|CE"
    assert parse_key(key) == ("NIFTY", date(2025, 11, 11), Decimal("25500"), CE)
    contract = option_instrument("NIFTY", date(2025, 11, 11), Decimal("25500"), PE, 65)
    assert contract.trading_symbol == "NIFTY 11 NOV 25500 PE"
    assert (contract.lot_size, contract.tick_size) == (65, Decimal("0.05"))


# --- chart timeframes -----------------------------------------------------------------------


def minute(at: time, o: str, h: str, low: str, c: str, day: date = date(2025, 11, 10)) -> Candle:
    return Candle(
        instrument_key="skill|NIFTY",
        interval="1m",
        start=ist_datetime(day, at),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def test_timeframes_fold_from_the_open() -> None:
    morning = [
        minute(time(9, 15), "100", "101", "99", "100"),
        minute(time(9, 19), "100", "105", "100", "104"),
        minute(time(9, 20), "104", "104", "98", "99"),
        minute(time(10, 14), "99", "99", "97", "98"),
        minute(time(10, 15), "98", "99", "98", "99"),
    ]
    five = fold(morning, "5m")
    assert [to_ist(c.start).time() for c in five] == [
        time(9, 15),
        time(9, 20),
        time(10, 10),
        time(10, 15),
    ]
    assert (five[0].high, five[0].close) == (Decimal(105), Decimal(104))
    hours = fold(morning, "1h")
    assert len(hours) == 2
    assert (hours[0].open, hours[0].high, hours[0].low, hours[0].close) == tuple(
        Decimal(v) for v in (100, 105, 97, 98)
    )
    assert hours[0].interval == "1h"
    day = fold(morning + [minute(time(9, 15), "90", "91", "89", "90", date(2025, 11, 11))], "1d")
    assert [(c.start, c.high, c.low) for c in day] == [
        (ist_datetime(date(2025, 11, 10), time(9, 15)), Decimal(105), Decimal(97)),
        (ist_datetime(date(2025, 11, 11), time(9, 15)), Decimal(91), Decimal(89)),
    ]
