"""Scheduled market events and headline tagging: the guard's view of news risk.

A scheduled event blocks new entries for a window around its time and widens the slippage
buffer for that day. Exits are never affected. Headlines only inform: they are leads, not
facts, so nothing here acts on one.
"""

import re
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pydantic import Field

from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.models import Frozen


class EventImpact(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


class MarketHoliday(Frozen):
    """A day the exchange is closed, and why."""

    day: date
    name: str


class MarketEvent(Frozen):
    day: date
    name: str
    at: time | None = None  # IST; None means no fixed time, so a warning but no entry block
    impact: EventImpact = EventImpact.HIGH
    block_before_minutes: int = Field(default=15, ge=0)
    block_after_minutes: int = Field(default=15, ge=0)


def blocking_event(events: tuple[MarketEvent, ...], now: datetime) -> MarketEvent | None:
    """The event whose no-entry window contains `now`, if any."""
    for event in events:
        if event.at is None or event.impact is not EventImpact.HIGH:
            continue
        moment = ist_datetime(event.day, event.at)
        start = moment - timedelta(minutes=event.block_before_minutes)
        end = moment + timedelta(minutes=event.block_after_minutes)
        if start <= now <= end:
            return event
    return None


def has_high_impact(events: tuple[MarketEvent, ...], day: date) -> bool:
    return any(e.day == day and e.impact is EventImpact.HIGH for e in events)


_TAGS = (
    ("rbi", r"\brbi\b|repo rate|monetary policy|\bmpc\b"),
    ("fed", r"\bfed\b|\bfomc\b|powell|federal reserve"),
    ("budget", r"union budget|\bbudget\b|finance minister"),
    ("inflation", r"\bcpi\b|\bwpi\b|inflation"),
    ("earnings", r"earnings|results|quarterly|\bq[1-4]\b|profit"),
    ("crude", r"crude|brent|\boil\b|opec"),
    ("geopolitics", r"\bwar\b|missile|border|sanction|strike on|conflict|tariff"),
    ("election", r"election|exit poll|lok sabha"),
    ("regulator", r"\bsebi\b|regulator|circular|\bstt\b|f&o rules"),
    ("flows", r"\bfiis?\b|\bdiis?\b|foreign investors|outflow"),
    ("global", r"wall street|nasdaq|dow jones|asian markets|global selloff"),
)


def tag_headline(text: str) -> tuple[str, ...]:
    return tuple(label for label, pattern in _TAGS if re.search(pattern, text, re.IGNORECASE))
