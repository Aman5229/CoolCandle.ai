from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from coolcandle.domain.models import Candle, Trade


class TrailingPolicy(Protocol):
    def next_stop(
        self, trade: Trade, candles: Sequence[Candle], ltp: Decimal
    ) -> Decimal | None: ...
