"""The agent service. Core is its only caller, with the shared service token.

Run: uv run uvicorn coolcandle_agent.main:create_app --factory --port 8001"""

import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from coolcandle_agent.anthropic_client import AnthropicClient
from coolcandle_agent.buddy import Buddy
from coolcandle_agent.config import Settings
from coolcandle_agent.contract import ChatAnswer, ChatRequest
from coolcandle_agent.core import HttpCore
from coolcandle_agent.llm import LlmClient

log = logging.getLogger(__name__)


def build(settings: Settings) -> Buddy:
    llm: LlmClient | None = None
    if settings.anthropic_api_key is not None:
        llm = AnthropicClient(settings.anthropic_api_key.get_secret_value(), settings.model)
    return Buddy(llm=llm, core=HttpCore(settings.core_url))


def create_app(settings: Settings | None = None, buddy: Buddy | None = None) -> FastAPI:
    config = settings or Settings()  # type: ignore[call-arg]
    service = buddy or build(config)
    expected = config.token.get_secret_value().encode()

    def from_core(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(token.encode(), expected):
            raise HTTPException(401, "Only CoolCandle core can chat here")

    app = FastAPI(title="CoolCandle.ai agent")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", dependencies=[Depends(from_core)])
    async def chat(body: ChatRequest) -> ChatAnswer:
        try:
            return await service.chat(body)
        except Exception as exc:
            # Usually the model API. Core turns this into a calm reply; only the type is logged.
            log.warning("chat failed: %s", type(exc).__name__)
            raise HTTPException(502, "The buddy could not answer") from None

    return app
