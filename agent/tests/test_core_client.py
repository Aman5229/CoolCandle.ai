"""The client for core's tools: what it sends, and what the model hears when core says no."""

import json

import httpx2

from coolcandle_agent.core import NO_ANSWER, HttpCore


def core(handler: httpx2.MockTransport) -> HttpCore:
    return HttpCore("http://api:8000/", transport=handler)


async def test_it_calls_the_tool_with_the_chats_token() -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"refused": "Trading is locked."})

    output, failed = await core(httpx2.MockTransport(answer)).call(
        "tool-token", "propose_trade", {"signal_id": "s1"}
    )
    assert (json.loads(output), failed) == ({"refused": "Trading is locked."}, False)
    sent = seen[0]
    assert str(sent.url) == "http://api:8000/internal/agent/tools/propose_trade"
    assert sent.headers["Authorization"] == "Bearer tool-token"
    assert json.loads(sent.content) == {"signal_id": "s1"}


async def test_cores_refusal_reaches_the_model_as_an_error() -> None:
    refusal = httpx2.Response(409, json={"code": "BLOCKED", "message": "Price left the band"})
    output, failed = await core(httpx2.MockTransport(lambda _: refusal)).call("t", "exit_trade", {})
    assert (json.loads(output), failed) == ({"error": "Price left the band"}, True)


async def test_an_ended_chat_or_a_strange_answer_is_no_answer() -> None:
    for response in (httpx2.Response(401, json={"message": 1}), httpx2.Response(500, text="boom")):
        output, failed = await core(httpx2.MockTransport(lambda _, r=response: r)).call(
            "t", "get_plan", {}
        )
        assert (json.loads(output), failed) == ({"error": NO_ANSWER}, True)


async def test_core_down_is_no_answer() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    output, failed = await core(httpx2.MockTransport(down)).call("t", "get_plan", {})
    assert (json.loads(output), failed) == ({"error": NO_ANSWER}, True)


async def test_an_unknown_tool_never_reaches_core() -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    output, failed = await core(httpx2.MockTransport(answer)).call("t", "../place_order", {})
    assert failed and "Unknown tool" in json.loads(output)["error"]
    assert seen == []
