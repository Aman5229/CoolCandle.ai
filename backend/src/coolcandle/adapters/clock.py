import time
from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    """A clock that starts at a chosen moment and runs `speed` times faster than real time."""

    def __init__(self, start: datetime, speed: float = 1.0) -> None:
        self._start = start
        self._began = time.monotonic()
        self.speed = speed

    def now(self) -> datetime:
        return self._start + timedelta(seconds=(time.monotonic() - self._began) * self.speed)


class ManualClock:
    """A clock that moves only when told to, for replays and tests."""

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def now(self) -> datetime:
        return self.moment

    def set(self, moment: datetime) -> None:
        self.moment = moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)
