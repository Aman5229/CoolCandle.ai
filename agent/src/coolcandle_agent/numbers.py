"""Code owns the numbers: find any number in a reply that no tool or user supplied."""

import re

_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _normalise(token: str) -> str:
    token = token.replace(",", "")
    return token.rstrip("0").rstrip(".") if "." in token else token


def numbers_in(text: str) -> set[str]:
    """Prices and quantities in `text`. Single digits and clock times do not count."""
    without_times = _TIME.sub(" ", text)
    found = {_normalise(token) for token in _NUMBER.findall(without_times)}
    return {token for token in found if len(token) > 1}


def invented_numbers(reply: str, sources: list[str]) -> set[str]:
    allowed: set[str] = set()
    for source in sources:
        allowed |= numbers_in(source)
    return numbers_in(reply) - allowed
