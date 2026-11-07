"""Records every tick: JSONL during the day, Parquet after the close. V2's training data."""

import json
import logging
from datetime import date
from pathlib import Path
from typing import IO

import pyarrow as pa
import pyarrow.parquet as pq

from coolcandle.domain.models import Instrument, Tick

log = logging.getLogger(__name__)

SCHEMA = pa.schema(
    [
        ("instrument_key", pa.string()),
        ("ts", pa.string()),
        ("received_at", pa.string()),
        ("ltp", pa.string()),
        ("bid", pa.string()),
        ("ask", pa.string()),
        ("volume_today", pa.int64()),
        ("oi", pa.int64()),
    ]
)


def day_dir(data_dir: Path, day: date) -> Path:
    return data_dir / "ticks" / day.isoformat()


def _text(value: object | None) -> str | None:
    return None if value is None else str(value)


class Recorder:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._day: date | None = None
        self._file: IO[str] | None = None

    def write(self, day: date, tick: Tick) -> None:
        if day != self._day:
            self.close()
            folder = day_dir(self.data_dir, day)
            folder.mkdir(parents=True, exist_ok=True)
            self._file = (folder / "ticks.jsonl").open("a", encoding="utf-8")
            self._day = day
        assert self._file is not None
        record = {
            "instrument_key": tick.instrument_key,
            "ts": tick.ts.isoformat(),
            "received_at": tick.received_at.isoformat(),
            "ltp": str(tick.ltp),
            "bid": _text(tick.bid),
            "ask": _text(tick.ask),
            "volume_today": tick.volume_today,
            "oi": tick.oi,
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")

    def save_instruments(self, day: date, instruments: list[Instrument]) -> None:
        """Replays need the day's contracts: strikes, lot sizes and expiries change."""
        folder = day_dir(self.data_dir, day)
        folder.mkdir(parents=True, exist_ok=True)
        rows = [instrument.model_dump(mode="json") for instrument in instruments]
        (folder / "instruments.json").write_text(json.dumps(rows))

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._day = None

    def flush_to_parquet(self, day: date) -> Path | None:
        """Convert a finished day. The JSONL stays until the Parquet file is complete."""
        self.close()
        folder = day_dir(self.data_dir, day)
        source = folder / "ticks.jsonl"
        if not source.exists():
            return None
        with source.open(encoding="utf-8") as lines:
            rows = [json.loads(line) for line in lines if line.strip()]
        target = folder / "ticks.parquet"
        partial = folder / "ticks.parquet.part"
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), partial, compression="zstd")
        partial.rename(target)
        source.unlink()
        log.info("recorded %s ticks for %s", len(rows), day)
        return target
