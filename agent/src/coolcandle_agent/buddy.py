"""The conversation: a manual tool loop, one history per trader per trading day.

Only an exchange the trader actually saw stays in the history. A refusal, a runaway loop, a reply
held back for an unsourced number or a failure in the middle is rolled back, so the model never
remembers saying something the trader did not read, and a half-finished tool call never breaks
the next turn."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from coolcandle_agent.contract import ChatAnswer, ChatRequest
from coolcandle_agent.core import CoreTools
from coolcandle_agent.llm import LlmClient
from coolcandle_agent.numbers import invented_numbers
from coolcandle_agent.prompt import SYSTEM, TOOLS

log = logging.getLogger(__name__)

MAX_STEPS = 6
NOT_SET_UP = "The buddy is not set up: no API key is configured."
REFUSED = "I can't help with that one."
TOO_LONG = "That took too many steps. Please ask again."
HELD_BACK = "I can only quote numbers from today's data. Ask me again, please."


@dataclass
class _Conversation:
    day: date
    messages: list[Any] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # every number the model may repeat
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Buddy:
    def __init__(self, *, llm: LlmClient | None, core: CoreTools) -> None:
        self.llm = llm
        self.core = core
        self._conversations: dict[str, _Conversation] = {}

    def _conversation(self, request: ChatRequest) -> _Conversation:
        talk = self._conversations.get(request.conversation)
        if talk is None or talk.day != request.trading_date:
            talk = self._conversations[request.conversation] = _Conversation(request.trading_date)
        return talk

    async def chat(self, request: ChatRequest) -> ChatAnswer:
        if self.llm is None:
            return ChatAnswer(text=NOT_SET_UP)
        talk = self._conversation(request)
        async with talk.lock:  # one exchange at a time per trader
            kept = len(talk.messages), len(talk.sources)
            seen = False
            try:
                answer, seen = await self._exchange(self.llm, talk, request)
                return answer
            finally:
                if not seen:
                    del talk.messages[kept[0] :]
                    del talk.sources[kept[1] :]

    async def _exchange(
        self, llm: LlmClient, talk: _Conversation, request: ChatRequest
    ) -> tuple[ChatAnswer, bool]:
        """The answer, and whether the trader sees the model's own words (so the exchange stays)."""
        talk.sources.append(request.message)
        talk.messages.append(llm.user_message(request.message))
        for _ in range(MAX_STEPS):
            turn = await llm.turn(SYSTEM, talk.messages, TOOLS)
            if turn.refused:
                return ChatAnswer(text=REFUSED), False
            talk.messages.append(turn.assistant_message)
            if not turn.tool_calls:
                invented = invented_numbers(turn.text, talk.sources)
                if invented:
                    # Code owns the numbers: a reply with an unsourced one never reaches the trader.
                    log.warning("reply held back: %s unsourced numbers", len(invented))
                    return ChatAnswer(text=HELD_BACK), False
                return ChatAnswer(text=turn.text), True
            results = []
            for call in turn.tool_calls:
                output, failed = await self.core.call(request.tool_token, call.name, call.input)
                if not failed:
                    talk.sources.append(output)
                results.append((call.id, output, failed))
            talk.messages.append(llm.tool_results(results))
        return ChatAnswer(text=TOO_LONG), False
