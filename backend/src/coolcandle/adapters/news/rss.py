"""Headlines from RSS feeds the owner configures. They are leads, not facts."""

import logging
from datetime import UTC
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx2

from coolcandle.ports.news_source import Headline

log = logging.getLogger(__name__)


def parse_rss(xml: str, source: str) -> list[Headline]:
    headlines = []
    for item in ElementTree.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        published = None
        stamp = item.findtext("pubDate")
        if stamp:
            try:
                published = parsedate_to_datetime(stamp).astimezone(UTC)
            except (TypeError, ValueError):
                published = None
        headlines.append(
            Headline(
                title=title,
                link=(item.findtext("link") or "").strip(),
                source=source,
                published_at=published,
            )
        )
    return headlines


class RssSource:
    def __init__(self, url: str) -> None:
        self.url = url

    async def latest(self) -> list[Headline]:
        async with httpx2.AsyncClient(timeout=10, follow_redirects=True) as http:
            response = await http.get(self.url)
        response.raise_for_status()
        return parse_rss(response.text, self.url)
