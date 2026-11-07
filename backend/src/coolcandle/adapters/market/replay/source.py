"""ReplayMarket: plays a recorded day through MarketData, for backtests and tests."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from coolcandle.domain.models import Candle, Instrument, Tick


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def tick_from_row(row: dict[str, Any]) -> Tick:
    return Tick(
        instrument_key=row["instrument_key"],
        ts=datetime.fromisoformat(row["ts"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        ltp=Decimal(row["ltp"]),
        bid=_decimal(row["bid"]),
        ask=_decimal(row["ask"]),
        volume_today=row["volume_today"],
        oi=row["oi"],
    )


def read_day(data_dir: Path, day: date) -> Iterator[Tick]:
    path = data_dir / "ticks" / day.isoformat() / "ticks.parquet"
    if not path.exists():
        return
    for batch in pq.ParquetFile(path).iter_batches():
        for row in batch.to_pylist():
            yield tick_from_row(row)


def read_instruments(data_dir: Path, day: date) -> list[Instrument]:
    path = data_dir / "ticks" / day.isoformat() / "instruments.json"
    return [Instrument.model_validate(row) for row in json.loads(path.read_text())]


def recorded_days(data_dir: Path) -> list[date]:
    folder = data_dir / "ticks"
    if not folder.exists():
        return []
    return sorted(
        date.fromisoformat(day.name) for day in folder.iterdir() if (day / "ticks.parquet").exists()
    )


class ReplayMarket:
    is_real = False

    def __init__(self, data_dir: Path, day: date, instruments: list[Instrument]) -> None:
        self.data_dir = data_dir
        self.day = day
        self._instruments = instruments

    async def instruments(self) -> list[Instrument]:
        return self._instruments

    async def subscribe(self, keys: list[str]) -> None:
        return None

    async def ticks(self) -> AsyncIterator[Tick]:
        for tick in read_day(self.data_dir, self.day):
            yield tick
            await asyncio.sleep(0)

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        return []
