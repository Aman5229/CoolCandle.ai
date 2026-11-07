"""Core's client for the agent service: what it sends, and how a bad answer becomes a calm one."""

import json
from datetime import date

import httpx2
import pytest

from coolcandle.adapters.agent.http import HttpAgent
from coolcandle.ports.agent import AgentAnswer, AgentRequest, AgentUnavailable

REQUEST = AgentRequest(
    conversation="u1", trading_date=date(2026, 11, 11), message="hi", tool_token="tool-secret"
)


def agent(handler: httpx2.MockTransport) -> HttpAgent:
    return HttpAgent("http://agent:8100/", "service-secret", transport=handler)


async def test_it_posts_the_request_with_the_service_token() -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"text": "Hello."})

    reply = await agent(httpx2.MockTransport(answer)).chat(REQUEST)
    assert reply == AgentAnswer(text="Hello.")
    sent = seen[0]
    assert str(sent.url) == "http://agent:8100/chat"
    assert sent.headers["Authorization"] == "Bearer service-secret"
    assert json.loads(sent.content) == {
        "conversation": "u1",
        "trading_date": "2026-11-11",
        "message": "hi",
        "tool_token": "tool-secret",
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(500, text="boom"),
        httpx2.Response(401, json={"detail": "no"}),
        httpx2.Response(200, json={"words": "not a reply"}),
        httpx2.Response(200, text="not json"),
    ],
    ids=["error", "refused", "wrong_shape", "not_json"],
)
async def test_a_bad_answer_is_unavailable(response: httpx2.Response) -> None:
    with pytest.raises(AgentUnavailable):
        await agent(httpx2.MockTransport(lambda _: response)).chat(REQUEST)


async def test_a_dead_service_is_unavailable_and_leaks_no_token() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(AgentUnavailable) as caught:
        await agent(httpx2.MockTransport(down)).chat(REQUEST)
    assert "secret" not in str(caught.value)
