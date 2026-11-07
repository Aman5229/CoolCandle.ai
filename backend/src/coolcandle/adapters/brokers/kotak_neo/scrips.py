"""Kotak's daily scrip master. Expiry counts from 1980 and strikes are in paise."""

import asyncio
import csv
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2

from coolcandle.domain.calendar import IST
from coolcandle.domain.models import Instrument, InstrumentKind, OptionType
from coolcandle.ports.broker import BrokerError

NSE_EPOCH_OFFSET = 315_532_800  # 1980-01-01T00:00:00Z in Unix seconds
INDICES = (
    Instrument(
        key="nse_cm|Nifty 50",
        exchange_segment="nse_cm",
        trading_symbol="NIFTY 50",
        kind=InstrumentKind.INDEX,
        underlying="NIFTY",
    ),
    Instrument(
        key="bse_cm|SENSEX",
        exchange_segment="bse_cm",
        trading_symbol="SENSEX",
        kind=InstrumentKind.INDEX,
        underlying="SENSEX",
    ),
)
SEGMENTS = {"nse_fo": "NIFTY", "bse_fo": "SENSEX"}


def expiry_date(seconds: int) -> date:
    return datetime.fromtimestamp(seconds + NSE_EPOCH_OFFSET, tz=UTC).astimezone(IST).date()


def paise_to_rupees(value: str) -> Decimal:
    return (Decimal(value) / 100).quantize(Decimal("0.01"))


def _clean(row: dict[str, str]) -> dict[str, str]:
    return {k.strip().rstrip(";"): (v or "").strip() for k, v in row.items() if k}


def parse_index_options(lines: Iterable[str], segment: str, underlying: str) -> list[Instrument]:
    options = []
    for raw in csv.DictReader(lines):
        row = _clean(raw)
        if row.get("pInstType") not in ("OPTIDX", "IO") or row.get("pSymbolName") != underlying:
            continue
        options.append(
            Instrument(
                key=f"{segment}|{row['pSymbol']}",
                exchange_segment=segment,
                trading_symbol=row["pTrdSymbol"],
                kind=InstrumentKind.OPTION,
                underlying=underlying,
                expiry=expiry_date(int(row["lExpiryDate"])),
                strike=paise_to_rupees(row["dStrikePrice"]),
                option_type=OptionType(row["pOptionType"]),
                lot_size=int(row["lLotSize"]),
                tick_size=paise_to_rupees(row["dTickSize"]),
            )
        )
    return options


async def download(client: Any, segment: str, folder: Path, day: date) -> Path:
    """Fetch today's file once and reuse it for the rest of the day."""
    path = folder / f"{segment}_{day.isoformat()}.csv"
    if path.exists():
        return path
    url = await asyncio.to_thread(client.scrip_master, exchange_segment=segment)
    if not isinstance(url, str):  # the SDK returns a URL string despite its type hint
        raise BrokerError(f"Kotak gave no scrip file for {segment}")
    async with httpx2.AsyncClient(timeout=60) as http:
        response = await http.get(url)
    response.raise_for_status()
    folder.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_bytes(response.content)
    partial.rename(path)
    return path


async def load_instruments(client: Any, folder: Path, day: date) -> list[Instrument]:
    instruments = list(INDICES)
    for segment, underlying in SEGMENTS.items():
        path = await download(client, segment, folder, day)
        with path.open(encoding="utf-8") as lines:
            instruments.extend(parse_index_options(lines, segment, underlying))
    return instruments
