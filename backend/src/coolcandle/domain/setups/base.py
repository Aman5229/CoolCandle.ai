"""What every setup produces. A hit is about the index; signals.py turns it into an option."""

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime

from coolcandle.domain.models import Frozen, JsonDict


class Direction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class SetupHit(Frozen):
    setup: str
    version: str
    underlying: str
    direction: Direction
    level: Decimal
    close: Decimal
    index_stop: Decimal
    at: AwareDatetime
    reason: str
    features: JsonDict = {}
