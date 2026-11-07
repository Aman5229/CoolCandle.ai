from datetime import date, time, timedelta
from decimal import Decimal

from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.models import Tick
from coolcandle.services.market.candles import CandleBuilder, bucket_start

DAY = date(2026, 11, 11)
OPEN = ist_datetime(DAY, time(9, 15))


def tick(seconds: float, price: str, volume: int | None = None) -> Tick:
    moment = OPEN + timedelta(seconds=seconds)
    return Tick(
        instrument_key="k",
        ts=moment,
        received_at=moment,
        ltp=Decimal(price),
        volume_today=volume,
    )


def test_buckets_line_up_with_the_open() -> None:
    assert bucket_start(OPEN + timedelta(minutes=7), 5) == OPEN + timedelta(minutes=5)


def test_a_candle_closes_on_the_first_tick_of_the_next_bucket() -> None:
    builder = CandleBuilder()
    assert builder.update(tick(1, "100")) == []
    assert builder.update(tick(30, "105")) == []
    assert builder.update(tick(40, "98")) == []
    closed = builder.update(tick(61, "101"))
    one_minute = next(c for c in closed if c.interval == "1m")
    assert (one_minute.open, one_minute.high, one_minute.low, one_minute.close) == (
        Decimal("100"),
        Decimal("105"),
        Decimal("98"),
        Decimal("98"),
    )


def test_volume_is_the_change_in_the_running_total() -> None:
    builder = CandleBuilder()
    builder.update(tick(1, "100", 1000))
    builder.update(tick(30, "101", 1500))
    closed = builder.update(tick(61, "102", 1600))
    assert closed[0].volume == 500


def test_forming_candles_are_shown_but_not_closed() -> None:
    builder = CandleBuilder()
    builder.update(tick(1, "100"))
    assert builder.closed("k", "1m") == []
    assert len(builder.with_forming("k", "1m")) == 1
