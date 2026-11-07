from typing import Protocol

from coolcandle.domain.models import Frozen


class Alert(Frozen):
    title: str
    body: str
    link: str | None = None


class Notifier(Protocol):
    async def send(self, alert: Alert) -> None: ...
