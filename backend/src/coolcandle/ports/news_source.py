from typing import Protocol

from pydantic import AwareDatetime

from coolcandle.domain.models import Frozen


class Headline(Frozen):
    title: str
    link: str
    source: str
    published_at: AwareDatetime | None = None


class NewsSource(Protocol):
    async def latest(self) -> list[Headline]: ...
