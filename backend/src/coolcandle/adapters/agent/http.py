"""The agent service, over HTTP on the private network."""

import httpx2
from pydantic import ValidationError

from coolcandle.ports.agent import AgentAnswer, AgentRequest, AgentUnavailable

TIMEOUT_S = 90  # a reply can take several model turns, each with tool calls


class HttpAgent:
    def __init__(
        self, url: str, token: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._url = url.rstrip("/") + "/chat"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport  # tests answer in-process

    async def chat(self, request: AgentRequest) -> AgentAnswer:
        try:
            async with httpx2.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as http:
                response = await http.post(
                    self._url, json=request.model_dump(mode="json"), headers=self._headers
                )
            response.raise_for_status()
            return AgentAnswer.model_validate_json(response.content)
        except (httpx2.HTTPError, ValidationError) as exc:
            # Only the type: the request carried the service token and a tool token.
            raise AgentUnavailable(type(exc).__name__) from None
