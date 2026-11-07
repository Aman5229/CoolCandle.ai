"""V3: the trading buddy. The model and the conversation live in the agent service (ADR 0004).
Core keeps everything the agent can reach: its tools, each usable only during the chat request
that started them, and the signal a reply puts on screen."""

import logging
import secrets
from dataclasses import dataclass
from typing import Any

from coolcandle.domain.calendar import MarketCalendar
from coolcandle.domain.models import Frozen, JsonDict
from coolcandle.domain.signals import Signal
from coolcandle.ports.agent import Agent, AgentRequest, AgentUnavailable
from coolcandle.ports.clock import Clock
from coolcandle.services.agent.tools import AgentTools
from coolcandle.services.auth.service import CurrentUser

log = logging.getLogger(__name__)


class AgentReply(Frozen):
    text: str
    proposal: Signal | None = None


class ToolTokenInvalid(Exception):
    """A tool call that does not belong to a chat in progress."""


@dataclass
class _Chat:
    user: CurrentUser
    proposal: Signal | None = None


class AgentService:
    def __init__(
        self, *, agent: Agent | None, tools: AgentTools, clock: Clock, calendar: MarketCalendar
    ) -> None:
        self.agent = agent
        self.tools = tools
        self.clock = clock
        self.calendar = calendar
        self._chats: dict[str, _Chat] = {}  # by tool token, only while the chat request runs

    async def chat(self, user: CurrentUser, text: str) -> AgentReply:
        if self.agent is None:
            return AgentReply(text="The buddy is not set up: no agent service is configured.")
        token = secrets.token_urlsafe(32)
        chat = self._chats[token] = _Chat(user)
        try:
            answer = await self.agent.chat(
                AgentRequest(
                    conversation=user.id,
                    trading_date=self.calendar.trading_date(self.clock.now()),
                    message=text,
                    tool_token=token,
                )
            )
        except AgentUnavailable as exc:
            log.warning("agent service unavailable: %s", exc)
            return AgentReply(text="The buddy can't be reached right now. Trading is not affected.")
        finally:
            del self._chats[token]
        # The card on screen is the signal core's own tool showed, never one the agent describes.
        return AgentReply(text=answer.text, proposal=chat.proposal)

    async def run_tool(self, token: str, name: str, payload: dict[str, Any]) -> JsonDict:
        chat = self._chats.get(token)
        if chat is None:
            raise ToolTokenInvalid
        result, proposal = await self.tools.run(chat.user, name, payload)
        if proposal is not None:
            chat.proposal = proposal
        return result
