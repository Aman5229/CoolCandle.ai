"""The buddy runs in its own service (ADR 0004). Core hands it one message at a time, and the
agent calls back into core for every fact and action. It never reaches the broker or the
database itself."""

from datetime import date
from typing import Protocol

from coolcandle.domain.models import Frozen


class AgentRequest(Frozen):
    conversation: str  # one per user: the agent keeps the history
    trading_date: date  # a new trading day starts a new conversation
    message: str
    tool_token: str  # lets the agent call core's tools, for this request only


class AgentAnswer(Frozen):
    text: str


class AgentUnavailable(Exception):
    """The agent service did not answer, or answered with something that is not a reply."""


class Agent(Protocol):
    async def chat(self, request: AgentRequest) -> AgentAnswer: ...
