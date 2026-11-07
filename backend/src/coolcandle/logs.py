"""Secrets never reach the logs: a filter scrubs records, and `redact` scrubs stored data."""

import logging
import re
from typing import Any

SECRET_FIELDS = frozenset(
    {
        "password",
        "mpin",
        "totp",
        "code",
        "token",
        "access_token",
        "consumer_key",
        "secret",
        "credentials",
        "confirm_token",
    }
)
_PATTERN = re.compile(
    r"""(?P<key>password|mpin|totp|token|consumer_key|secret)(?P<sep>["']?\s*[=:]\s*["']?)[^\s"',}]+""",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    return _PATTERN.sub(lambda m: f"{m['key']}{m['sep']}[removed]", text)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in SECRET_FIELDS:
            clean[key] = "[removed]"
        elif isinstance(value, dict):
            clean[key] = redact(value)
        else:
            clean[key] = value
    return clean


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = scrub(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        if record.exc_info:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Not uvicorn.access: its formatter needs the raw args, and paths carry no secrets.
    for name in (None, "uvicorn", "uvicorn.error"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(SecretFilter())
