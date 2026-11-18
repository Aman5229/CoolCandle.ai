"""Past index minutes on disk, for Skill Test: Parquet files of 1-minute candles, one per index
and month, under data/skill/history/<INDEX>/1m/<YYYY-MM>.parquet. Prices are stored exactly, as
whole hundredths (25012.35 is 2501235).

Longer timeframes are folded here, inside pyarrow, because a year of minutes as Python objects
would take over a hundred megabytes. Bars start at the 09:15 open, the same rule as
`domain.skill.charts.fold`, which folds the minutes of the day being played."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from coolcandle.domain.calendar import to_ist
from coolcandle.domain.models import Candle
from coolcandle.domain.skill.charts import INTERVALS
from coolcandle.domain.skill.options import index_key

SCHEMA = pa.schema(
    [
        ("start", pa.int64()),
        ("open", pa.int64()),
        ("high", pa.int64()),
        ("low", pa.int64()),
        ("close", pa.int64()),
    ]
)
_MS = 1000
_DAY_MS = 86_400_000
_IST_MS = 19_800_000  # +05:30
_OPEN_MS = (9 * 60 + 15) * 60_000  # 09:15 after midnight
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CENT = Decimal("0.01")
_EPOCH_DAY = _EPOCH.date().toordinal()  # day numbers count from 1 Jan 1970


def _ms(moment: datetime) -> int:
    return int((moment - _EPOCH) / timedelta(milliseconds=1))


def _hundredths(price: Decimal) -> int:
    return int(price.quantize(CENT) * 100)


def _month(moment: datetime) -> date:
    return to_ist(moment).date().replace(day=1)


class IndexHistory:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "skill" / "history"
        self._days: dict[str, list[date]] = {}

    def path(self, index: str, month: date) -> Path:
        return self.root / index / "1m" / f"{month:%Y-%m}.parquet"

    def _files(self, index: str) -> list[Path]:
        folder = self.root / index / "1m"
        return sorted(folder.glob("*.parquet")) if folder.exists() else []

    # --- writing ----------------------------------------------------------------------------

    def save(self, index: str, candles: Iterable[Candle]) -> int:
        """Merge candles into their month files. A minute already stored is replaced. Returns
        how many minutes the index now has in those months."""
        by_month: dict[date, dict[int, Candle]] = {}
        for candle in candles:
            by_month.setdefault(_month(candle.start), {})[_ms(candle.start)] = candle
        total = 0
        for month, fresh in by_month.items():
            path = self.path(index, month)
            rows = {r["start"]: r for r in pq.read_table(path).to_pylist()} if path.exists() else {}
            for stamp, c in fresh.items():
                rows[stamp] = {
                    "start": stamp,
                    "open": _hundredths(c.open),
                    "high": _hundredths(c.high),
                    "low": _hundredths(c.low),
                    "close": _hundredths(c.close),
                }
            ordered = [rows[stamp] for stamp in sorted(rows)]
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            pq.write_table(pa.Table.from_pylist(ordered, schema=SCHEMA), temp, compression="zstd")
            temp.replace(path)  # a crash mid-write never leaves half a month
            total += len(ordered)
        self._days.pop(index, None)
        return total

    # --- reading ----------------------------------------------------------------------------

    def days(self, index: str) -> list[date]:
        """Every trading day with minutes on disk, oldest first."""
        if index not in self._days:
            found: set[date] = set()
            for path in self._files(index):
                stamps = pq.read_table(path, columns=["start"])["start"]
                ist_days = pc.divide(pc.add(stamps, _IST_MS), _DAY_MS)
                found.update(
                    date.fromordinal(_EPOCH_DAY + int(d)) for d in pc.unique(ist_days).to_pylist()
                )
            self._days[index] = sorted(found)
        return self._days[index]

    def _table(self, index: str, start: datetime, end: datetime) -> pa.Table:
        month = _month(start)
        last = _month(end - timedelta(milliseconds=1))
        tables = []
        while month <= last:
            path = self.path(index, month)
            if path.exists():
                tables.append(pq.read_table(path))
            month = (month + timedelta(days=32)).replace(day=1)
        if not tables:
            return SCHEMA.empty_table()
        table = pa.concat_tables(tables)
        inside = pc.and_(
            pc.greater_equal(table["start"], _ms(start)), pc.less(table["start"], _ms(end))
        )
        return table.filter(inside).sort_by("start")

    def window(self, index: str, start: datetime, end: datetime) -> list[Candle]:
        """The 1-minute candles that start in [start, end)."""
        return self._candles(index, "1m", self._table(index, start, end))

    def bars(self, index: str, interval: str, start: datetime, end: datetime) -> list[Candle]:
        """Candles in `interval`, folded from the minutes that start in [start, end)."""
        table = self._table(index, start, end)
        if interval == "1m" or table.num_rows == 0:
            return self._candles(index, interval, table)
        stamps = table["start"]
        local = pc.add(stamps, _IST_MS)
        day_start = pc.multiply(pc.divide(local, _DAY_MS), _DAY_MS)
        opens = pc.subtract(pc.add(day_start, _OPEN_MS), _IST_MS)  # 09:15 IST, in UTC ms
        if interval == "1d":
            bucket = opens
        else:
            size = INTERVALS[interval] * 60_000
            since = pc.subtract(stamps, opens)
            bucket = pc.add(opens, pc.multiply(pc.divide(since, size), size))
        grouped = (
            table.append_column("bucket", bucket)
            .group_by("bucket", use_threads=False)
            .aggregate([("open", "first"), ("high", "max"), ("low", "min"), ("close", "last")])
            .sort_by("bucket")
        )
        renamed = pa.table(
            {
                "start": grouped["bucket"],
                "open": grouped["open_first"],
                "high": grouped["high_max"],
                "low": grouped["low_min"],
                "close": grouped["close_last"],
            }
        )
        return self._candles(index, interval, renamed)

    def _candles(self, index: str, interval: str, table: pa.Table) -> list[Candle]:
        key = index_key(index)
        return [
            Candle(
                instrument_key=key,
                interval=interval,
                start=_EPOCH + timedelta(milliseconds=row["start"]),
                open=Decimal(row["open"]) / 100,
                high=Decimal(row["high"]) / 100,
                low=Decimal(row["low"]) / 100,
                close=Decimal(row["close"]) / 100,
            )
            for row in table.to_pylist()
        ]
