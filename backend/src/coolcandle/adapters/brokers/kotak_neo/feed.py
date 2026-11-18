"""Kotak's live market feed and history. Indices and options subscribe differently."""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

from neo_api_client.websocket.feed import WsToken

from coolcandle.adapters.brokers.kotak_neo.broker import KotakBroker
from coolcandle.adapters.brokers.kotak_neo.history import HistoryUnavailable
from coolcandle.adapters.brokers.kotak_neo.history import history as kotak_history
from coolcandle.adapters.brokers.kotak_neo.mapping import tick_from_message
from coolcandle.adapters.brokers.kotak_neo.scrips import load_instruments
from coolcandle.domain.models import Candle, Instrument, Tick
from coolcandle.ports.clock import Clock

log = logging.getLogger(__name__)

INDEX_TOKENS = {"nse_cm|Nifty 50": ("nse_cm", "Nifty 50"), "bse_cm|SENSEX": ("bse_cm", "SENSEX")}
RECONNECT_PAUSE_S = 5


class KotakMarketData:
    is_real = True

    def __init__(self, broker: KotakBroker, clock: Clock, data_dir: Path) -> None:
        self.broker = broker
        self.clock = clock
        self.folder = data_dir / "scripmaster"
        self._keys: set[str] = set(INDEX_TOKENS)
        self._changed = asyncio.Event()

    async def instruments(self) -> list[Instrument]:
        return await load_instruments(self.broker.client, self.folder, self.clock.now().date())

    async def subscribe(self, keys: list[str]) -> None:
        added = set(keys) - self._keys
        if added:
            self._keys |= added
            self._changed.set()

    async def ticks(self) -> AsyncIterator[Tick]:
        while True:
            try:
                async with self.broker.client.create_websocket(max_reconnect_attempts=50) as ws:
                    indices = [WsToken(*INDEX_TOKENS[k]) for k in self._keys if k in INDEX_TOKENS]
                    options = [
                        WsToken(*key.split("|", 1)) for key in self._keys if key not in INDEX_TOKENS
                    ]
                    await ws.subscribe_index(indices)
                    if options:
                        await ws.subscribe_scrips(options)
                    self._changed.clear()
                    async for message in ws:
                        tick = tick_from_message(message, self.clock.now())
                        if tick is not None:
                            yield tick
                        if self._changed.is_set():
                            break  # reconnect with the new strikes
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the Kotak feed dropped; reconnecting")
                await asyncio.sleep(RECONNECT_PAUSE_S)

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        """Candles in [start, end]. Kotak rate-limits hard, so this goes slowly and backs off. A
        chart without its backfill is better than a feed that fails to start."""
        try:
            return await kotak_history(self.broker.client, key, interval, start, end)
        except HistoryUnavailable as exc:
            log.warning("chart backfill skipped: %s", exc)
            return []

    async def order_feed_signals(self, wake: asyncio.Event) -> None:
        """Any order-feed message wakes the order sync, so a fill is seen at once."""
        while True:
            try:
                async with self.broker.client.create_order_feed() as feed:
                    async for _ in feed:
                        wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the Kotak order feed dropped; reconnecting")
                await asyncio.sleep(RECONNECT_PAUSE_S)
