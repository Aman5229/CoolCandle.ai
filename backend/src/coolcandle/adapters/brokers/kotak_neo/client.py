"""Kotak Neo login. A login from anywhere ends every other session on the account."""

from typing import Any

from neo_api_client.neo_api import NeoAPI

from coolcandle.ports.broker import BrokerError


def _check(step: str, response: Any) -> None:
    # Kotak can answer HTTP 200 with an error body, so read the payload itself.
    if not isinstance(response, dict) or "error" in response or "data" not in response:
        raise BrokerError(f"Kotak {step} failed. Check the credentials and the TOTP")


def login(credentials: dict[str, str], totp: str) -> NeoAPI:
    client = NeoAPI(consumer_key=credentials["consumer_key"], environment="prod")
    _check(
        "login",
        client.totp_login(
            mobile_number=credentials["mobile_number"], ucc=credentials["ucc"], totp=totp
        ),
    )
    _check("MPIN check", client.totp_validate(mpin=credentials["mpin"]))
    return client
