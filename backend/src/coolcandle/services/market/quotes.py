"""The latest price of everything, in memory."""

from datetime import datetime

from coolcandle.domain.models import Tick


class QuoteBook:
    def __init__(self) -> None:
        self._latest: dict[str, Tick] = {}
        self.last_tick_at: datetime | None = None

    def update(self, tick: Tick) -> None:
        self._latest[tick.instrument_key] = tick
        self.last_tick_at = tick.received_at

    def get(self, key: str) -> Tick | None:
        return self._latest.get(key)

    def feed_age(self, now: datetime) -> float | None:
        if self.last_tick_at is None:
            return None
        return max((now - self.last_tick_at).total_seconds(), 0.0)
