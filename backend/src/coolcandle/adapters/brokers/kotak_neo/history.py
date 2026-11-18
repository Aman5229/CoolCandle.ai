"""Kotak's historical candles. Read-only, and it needs only the consumer key: no TOTP and no
login, so it never ends a trading session. Kotak rate-limits hard (three quick requests earn an
HTTP 429), so it goes slowly and backs off. Symbols: `nse_cm|Nifty 50`, `bse_cm|SENSEX`."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from coolcandle.domain.models import Candle

log = logging.getLogger(__name__)

CHUNK_DAYS = {"1min": 28, "5min": 28, "D": 175}  # Kotak's limits per request: 30 and 180 days
INTERVALS = {"1m": "1min", "5m": "5min", "1d": "D"}
PAUSE_S = 2.0
TRIES = 6

Sleep = Callable[[float], Awaitable[None]]


async def history(
    client: Any,
    key: str,
    interval: str,
    start: date,
    end: date,
    *,
    candle_key: str | None = None,
    sleep: Sleep = asyncio.sleep,
) -> list[Candle]:
    """Candles in [start, end], oldest first. `candle_key` names them, if not by `key`."""
    name = INTERVALS[interval]
    candles: dict[datetime, Candle] = {}
    repaired = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS[name] - 1), end)
        for row in await _fetch(client, key, name, cursor, chunk_end, sleep):
            candle, fixed = _candle(candle_key or key, interval, row)
            candles[candle.start] = candle
            repaired += fixed
        cursor = chunk_end + timedelta(days=1)
        await sleep(PAUSE_S)
    if repaired:
        log.warning("%s: %d minutes had an open or close outside their high and low", key, repaired)
    return [candles[moment] for moment in sorted(candles)]


def _candle(key: str, interval: str, row: list[Any]) -> tuple[Candle, bool]:
    """One of Kotak's rows. Now and then Kotak sends a minute whose open or close lies outside its
    own high and low; the high and low are widened to hold them, rather than lose the minute.
    The bool says whether that happened."""
    ts, open_, high, low, close, volume = row[:6]
    o, h, low_, c = (Decimal(str(value)) for value in (open_, high, low, close))
    fixed = h < max(o, c) or low_ > min(o, c)
    candle = Candle(
        instrument_key=key,
        interval=interval,
        start=datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z"),
        open=o,
        high=max(h, o, c),
        low=min(low_, o, c),
        close=c,
        volume=int(volume or 0),
    )
    return candle, fixed


class HistoryUnavailable(Exception):
    """Kotak did not answer after every retry. What arrived before is kept: fetch again later."""


async def _fetch(
    client: Any, key: str, interval: str, start: date, end: date, sleep: Sleep
) -> list[list[Any]]:
    """One request, retried with a growing wait when Kotak rate-limits or does not answer in
    time. It only reads, so trying again is safe. A clear refusal (say, no data for a symbol)
    is an empty answer, not a failure."""
    wait = PAUSE_S
    for _ in range(TRIES):
        try:
            response = await asyncio.to_thread(
                client.historical_data,
                neosymbol=key,
                interval=interval,
                from_date=start.isoformat(),
                to_date=end.isoformat(),
            )
        except Exception as exc:  # the SDK raises on timeouts and dropped connections
            log.warning(
                "Kotak did not answer for %s %s to %s (%s)", key, start, end, type(exc).__name__
            )
            response = None
        retry = response is None or (
            isinstance(response, dict)
            and 429 in (response.get("code"), response.get("status_code"))
        )
        if retry:
            wait = min(wait * 2, 120)
            await sleep(wait)
            continue
        if isinstance(response, dict) and str(response.get("status")).lower() == "success":
            found: list[list[Any]] = response.get("data", {}).get("candles") or []
            return found
        log.warning("history failed for %s %s", key, interval)
        return []
    raise HistoryUnavailable(f"Kotak did not answer for {key} from {start} to {end}")
