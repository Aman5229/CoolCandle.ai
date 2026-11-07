"""The buddy. The browser chats through core; the agent service calls core's tools back.

`internal` is mounted outside /api. Caddy only forwards /api and /ws, so it is reachable from the
private network alone, and each call needs the tool token of a chat that is still in progress."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import ChatBody
from coolcandle.domain.models import JsonDict
from coolcandle.services.agent.service import AgentReply, ToolTokenInvalid

router = APIRouter()
internal = APIRouter(prefix="/internal/agent")


@router.post("/agent/chat", dependencies=Protected)
async def chat(body: ChatBody, request: Request, user: User) -> AgentReply:
    reply: AgentReply = await request.app.state.container.agent.chat(user, body.message)
    return reply


@internal.post("/tools/{name}")
async def tool(
    name: str,
    payload: dict[str, Any],
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JsonDict:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not token:
        raise ApiError(401, "TOOL_TOKEN", "A tool call needs the token of a chat in progress")
    try:
        result: JsonDict = await request.app.state.container.agent.run_tool(token, name, payload)
    except ToolTokenInvalid:
        raise ApiError(401, "TOOL_TOKEN", "That chat has ended") from None
    return result
