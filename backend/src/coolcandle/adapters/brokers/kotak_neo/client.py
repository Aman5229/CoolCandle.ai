"""Kotak Neo login. A login from anywhere ends every other session on the account."""

from typing import Any

import httpx
from neo_api_client.neo_api import NeoAPI

from coolcandle.ports.broker import BrokerError


def _check(step: str, response: Any) -> None:
    # Kotak can answer HTTP 200 with an error body, so read the payload itself.
    if not isinstance(response, dict) or "error" in response or "data" not in response:
        raise BrokerError(f"Kotak {step} failed. Check the credentials and the TOTP")


def ipv4_transport() -> httpx.HTTPTransport:
    """Kotak takes orders only from the IPv4 address registered in its Trade API settings, but
    its servers answer on IPv6 too, and a machine with IPv6 prefers it: orders then arrive from
    an address Kotak doesn't know and are refused as "unauthorized". Binding the local side to
    0.0.0.0 keeps every call on IPv4."""
    return httpx.HTTPTransport(local_address="0.0.0.0")


def login(credentials: dict[str, str], totp: str) -> NeoAPI:
    client = NeoAPI(
        consumer_key=credentials["consumer_key"], environment="prod", transport=ipv4_transport()
    )
    _check(
        "login",
        client.totp_login(
            mobile_number=credentials["mobile_number"], ucc=credentials["ucc"], totp=totp
        ),
    )
    _check("MPIN check", client.totp_validate(mpin=credentials["mpin"]))
    return client
