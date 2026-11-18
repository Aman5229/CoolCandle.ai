"""Fresh tables and an admin for the browser test, and a year of NIFTY history for Skill Test, so
the test never calls Kotak. Prints the authenticator secret.

The year is sparse (four minutes a day) except Monday 10 November 2025, the day the test plays: a
quiet 25,000 until one minute at 09:20 reaches both 24,900 and 25,200. A call bought just before,
with its stop 10 below and its target 20 above, must be stopped, not paid."""

import asyncio
import math
import sys
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import create_engine

from coolcandle.adapters.market.history.store import IndexHistory
from coolcandle.config import Settings
from coolcandle.db.tables import Base
from coolcandle.domain.calendar import ist_datetime, to_ist
from coolcandle.domain.models import Candle
from coolcandle.main import build

FIRST = date(2024, 11, 8)
LAST = date(2025, 11, 21)
PLAYED = date(2025, 11, 10)
MINUTE = timedelta(minutes=1)


def bar(index: str, day: date, at: time, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        instrument_key=f"skill|{index}",
        interval="1m",
        start=ist_datetime(day, at),
        open=Decimal(f"{o:.2f}"),
        high=Decimal(f"{h:.2f}"),
        low=Decimal(f"{low:.2f}"),
        close=Decimal(f"{c:.2f}"),
    )


def year() -> list[Candle]:
    candles = []
    day, n = FIRST, 0
    while day <= LAST:
        if day.weekday() < 5 and day != PLAYED:
            level = 24000 + 900 * math.sin(n / 40)
            for i, at in enumerate((time(9, 15), time(11, 0), time(13, 0), time(15, 29))):
                o = level + 30 * math.sin(n + i)
                c = level + 30 * math.sin(n + i + 1)
                candles.append(bar("NIFTY", day, at, o, max(o, c) + 20, min(o, c) - 20, c))
            n += 1
        day += timedelta(days=1)
    return candles


def played_day() -> list[Candle]:
    candles = []
    close = 25000.0
    opens = ist_datetime(PLAYED, time(9, 15))
    for i in range(375):  # 09:15 to 15:29
        at = to_ist(opens + i * MINUTE).time()
        if at == time(9, 20):
            candles.append(bar("NIFTY", PLAYED, at, close, 25200, 24900, 25150))
            close = 25150.0
            continue
        base = 25000 if at < time(9, 20) else 25150
        nxt = base + round(2 * math.sin(i / 3), 2)
        candles.append(bar("NIFTY", PLAYED, at, close, max(close, nxt) + 1, min(close, nxt) - 1, nxt))
        close = nxt
    return candles


def vix() -> list[Candle]:
    opens = ist_datetime(PLAYED, time(9, 15))
    return [
        bar("INDIAVIX", PLAYED, to_ist(opens + i * MINUTE).time(), 14, 14, 14, 14)
        for i in range(375)
    ]


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    path = settings.database_url.split("///")[1]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    history = IndexHistory(settings.data_dir)
    history.save("NIFTY", [*year(), *played_day()])
    history.save("INDIAVIX", vix())
    container = build(settings)
    print(await container.auth.create_admin(sys.argv[1], sys.argv[2]))


asyncio.run(main())
