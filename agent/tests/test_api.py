"""The service's door: only core, with the shared token, can start a chat."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from coolcandle_agent.buddy import Buddy
from coolcandle_agent.config import Settings
from coolcandle_agent.contract import ChatAnswer, ChatRequest
from coolcandle_agent.main import create_app
from tests.fakes import FakeCore, ScriptedLlm

TOKEN = "a-service-token-that-is-long-enough-1234"
# The same request core's own test sends (backend/tests/agent/test_http_agent.py).
EXAMPLE = {
    "conversation": "u1",
    "trading_date": "2026-11-11",
    "message": "hi",
    "tool_token": "tool-secret",
}


def settings(**changes: Any) -> Settings:
    return Settings(_env_file=None, token=SecretStr(TOKEN), **changes)  # type: ignore[call-arg]


def client(buddy: Buddy | None = None) -> TestClient:
    chosen = buddy or Buddy(llm=ScriptedLlm([], "Hello."), core=FakeCore())
    return TestClient(create_app(settings(), buddy=chosen))


def test_health() -> None:
    assert client().get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("header", [None, "Bearer wrong", TOKEN, f"Basic {TOKEN}"])
def test_only_core_can_chat(header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    assert client().post("/chat", json=EXAMPLE, headers=headers).status_code == 401


def test_core_gets_an_answer() -> None:
    response = client().post("/chat", json=EXAMPLE, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert ChatAnswer.model_validate(response.json()) == ChatAnswer(text="Hello.")


def test_a_failure_is_a_plain_502() -> None:
    class Broken(Buddy):
        async def chat(self, request: ChatRequest) -> ChatAnswer:
            raise RuntimeError("model API said: key sk-123 is invalid")

    response = client(Broken(llm=None, core=FakeCore())).post(
        "/chat", json=EXAMPLE, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 502
    assert "sk-123" not in response.text


def test_a_short_service_token_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, token=SecretStr("short"))  # type: ignore[call-arg]


def test_without_a_key_there_is_no_model() -> None:
    from coolcandle_agent.main import build

    assert build(settings()).llm is None
    assert build(settings(anthropic_api_key=SecretStr("sk-test"))).llm is not None
