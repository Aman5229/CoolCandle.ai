from typing import Protocol

from coolcandle.domain.models import Candle
from coolcandle.domain.setups.base import SetupHit


class SignalSource(Protocol):
    @property
    def name(self) -> str: ...

    def check(
        self, underlying: str, today: list[Candle], history: list[Candle]
    ) -> SetupHit | None: ...
