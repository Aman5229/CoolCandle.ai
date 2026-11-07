"""A recorded day goes in, and the real guard, orders and paper fills produce the result."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine

from coolcandle.adapters.market.replay.source import read_day, read_instruments, recorded_days
from coolcandle.backtesting import SETUPS, run_backtest, run_day
from coolcandle.config import Settings
from coolcandle.db.tables import Base
from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.costs import round_trip
from coolcandle.domain.models import Tick
from coolcandle.main import cost_rates
from coolcandle.services.recorder.recorder import Recorder
from tests.conftest import CALL, DAY, LOT, NIFTY, instrument_list, make_settings


def tick(key: str, moment: datetime, ltp: str) -> Tick:
    price = Decimal(ltp)
    return Tick(
        instrument_key=key,
        ts=moment,
        received_at=moment,
        ltp=price,
        bid=price - Decimal("0.05"),
        ask=price + Decimal("0.05"),
    )


def record_breakout_day(data_dir: Path) -> None:
    """An opening range of 24500 to 24520, a close above it, then the option runs to target."""
    recorder = Recorder(data_dir)
    recorder.save_instruments(DAY, instrument_list())
    start = ist_datetime(DAY, time(9, 15))
    for minute in range(30):
        moment = start + timedelta(minutes=minute)
        index = ("24500", "24520")[minute % 2] if minute < 15 else "24510"
        recorder.write(DAY, tick(NIFTY, moment, index))
        recorder.write(DAY, tick(CALL, moment, "100"))
    for minute in range(30, 36):  # the 09:45 bar closes at 24530, beyond the range
        moment = start + timedelta(minutes=minute)
        recorder.write(DAY, tick(NIFTY, moment, "24530"))
        recorder.write(DAY, tick(CALL, moment, "100"))
    moment = start + timedelta(minutes=36)
    for step in range(60):
        moment += timedelta(seconds=5)
        recorder.write(DAY, tick(NIFTY, moment, "24530"))
        recorder.write(DAY, tick(CALL, moment, str(Decimal("100") + step)))
    recorder.flush_to_parquet(DAY)


def settings_for(data_dir: Path) -> Settings:
    return make_settings(database_url="sqlite+aiosqlite:///:memory:", data_dir=data_dir)


def test_recording_round_trips_through_parquet(tmp_path: Path) -> None:
    record_breakout_day(tmp_path)
    assert recorded_days(tmp_path) == [DAY]
    assert len(read_instruments(tmp_path, DAY)) == 3
    first = next(read_day(tmp_path, DAY))
    assert first.ltp == Decimal("24500")
    assert not (tmp_path / "ticks" / str(DAY) / "ticks.jsonl").exists()


async def test_a_recorded_day_runs_through_the_real_system(tmp_path: Path) -> None:
    record_breakout_day(tmp_path)
    results = await run_day(settings_for(tmp_path), SETUPS["opening_range"], DAY)

    assert len(results) == 1
    assert results[0].net_pnl > 0
    assert results[0].r_multiple > 0


async def test_costs_come_out_of_the_result(tmp_path: Path) -> None:
    record_breakout_day(tmp_path)
    settings = settings_for(tmp_path)
    results = await run_day(settings, SETUPS["opening_range"], DAY)
    gross = results[0].net_pnl + round_trip(
        Decimal("100.05"), Decimal("120.95"), LOT, cost_rates(settings)
    )
    assert results[0].net_pnl < gross
    assert gross > 0


async def test_no_recordings_gives_a_clear_answer(tmp_path: Path) -> None:
    summary = await run_backtest(
        settings_for(tmp_path), "opening_range", date(2026, 1, 1), DAY, Decimal("5000")
    )
    assert "No recorded days" in summary


async def test_one_day_can_never_pass_the_gate(tmp_path: Path) -> None:
    record_breakout_day(tmp_path)
    path = tmp_path / "log.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    settings = make_settings(database_url=f"sqlite+aiosqlite:///{path}", data_dir=tmp_path)
    summary = await run_backtest(settings, "opening_range", DAY, DAY, Decimal("5000"))
    assert "Gate: FAILED" in summary
