"""Clock-driven daily jobs. Each runs once per trading day, at or after its time."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, time

from coolcandle.domain.calendar import MarketCalendar, to_ist
from coolcandle.ports.clock import Clock

log = logging.getLogger(__name__)


@dataclass
class Job:
    at: time
    name: str
    run: Callable[[], Awaitable[None]]
    last_day: date | None = None


class Scheduler:
    def __init__(self, clock: Clock, calendar: MarketCalendar, jobs: list[Job]) -> None:
        self.clock = clock
        self.calendar = calendar
        self.jobs = jobs

    async def tick(self) -> None:
        local = to_ist(self.clock.now())
        if not self.calendar.is_trading_day(local.date()):
            return
        for job in self.jobs:
            if local.time() >= job.at and job.last_day != local.date():
                job.last_day = local.date()
                try:
                    await job.run()
                except Exception:
                    log.exception("scheduled job failed: %s", job.name)
