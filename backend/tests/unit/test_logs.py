"""MPIN, TOTP, tokens and keys never appear in logs or stored audit data."""

import logging

import pytest

from coolcandle.logs import SecretFilter, redact, scrub


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2",
        'mpin: "246810"',
        "consumer_key=CK123 failed",
        "{'totp': '123456'}",
        "token=abc.def",
    ],
)
def test_secrets_are_scrubbed_from_text(text: str) -> None:
    cleaned = scrub(text)
    assert "[removed]" in cleaned
    for value in ("hunter2", "246810", "CK123", "123456", "abc.def"):
        assert value not in cleaned


def test_ordinary_text_is_left_alone() -> None:
    assert scrub("day locked: MAX_TRADES (paper)") == "day locked: MAX_TRADES (paper)"


def test_stored_data_is_redacted_at_any_depth() -> None:
    clean = redact({"mpin": "1", "nested": {"token": "2", "ok": 3}, "reason": "x"})
    assert clean == {"mpin": "[removed]", "nested": {"token": "[removed]", "ok": 3}, "reason": "x"}


def record(message: str, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, "f", 1, message, None, exc_info)  # type: ignore[arg-type]


def test_a_log_message_is_scrubbed() -> None:
    item = record("login failed password=hunter2")
    SecretFilter().filter(item)
    assert "hunter2" not in item.getMessage()


def test_a_traceback_is_scrubbed() -> None:
    try:
        raise RuntimeError("request failed: mpin=246810")
    except RuntimeError:
        import sys

        item = record("boom", sys.exc_info())
    SecretFilter().filter(item)
    assert item.exc_info is None
    assert item.exc_text is not None
    assert "246810" not in item.exc_text


def test_a_clean_record_keeps_its_args_for_other_formatters() -> None:
    item = logging.LogRecord("t", logging.INFO, "f", 1, "%s %s", ("GET", "/api/health"), None)
    SecretFilter().filter(item)
    assert item.args == ("GET", "/api/health")
