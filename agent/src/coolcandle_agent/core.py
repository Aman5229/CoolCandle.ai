"""CoolCandle core's tools, over HTTP on the private network. Each call carries the tool token of
the chat that asked, and core refuses it once that chat is over."""

import json
import logging
from typing import Any, Protocol

import httpx2

from coolcandle_agent.llm import JsonDict
from coolcandle_agent.prompt import TOOL_NAMES

log = logging.getLogger(__name__)

TIMEOUT_S = 15
NO_ANSWER = "CoolCandle did not answer. Tell the trader you can't check right now."


class CoreTools(Protocol):
    async def call(self, token: str, name: str, payload: JsonDict) -> tuple[str, bool]:
        """The tool's JSON result for the model, and whether it failed."""
        ...


def _failure(message: str) -> tuple[str, bool]:
    return json.dumps({"error": message}), True


class HttpCore:
    def __init__(self, url: str, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._url = url.rstrip("/") + "/internal/agent/tools/"
        self._transport = transport  # tests answer in-process

    async def call(self, token: str, name: str, payload: JsonDict) -> tuple[str, bool]:
        if name not in TOOL_NAMES:  # never let a model-chosen name shape the URL
            return _failure(f"Unknown tool {name}")
        try:
            async with httpx2.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as http:
                response = await http.post(
                    self._url + name, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx2.HTTPError as exc:
            log.warning("core tool %s failed: %s", name, type(exc).__name__)
            return _failure(NO_ANSWER)
        if response.is_success:
            return response.text, False
        return _failure(_message(response))


def _message(response: httpx2.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return str(body["message"])
    return NO_ANSWER
