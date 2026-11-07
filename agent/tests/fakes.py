"""Stand-ins for the model and for CoolCandle core."""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from coolcandle_agent.contract import ChatRequest
from coolcandle_agent.llm import JsonDict, LlmTurn, ToolCall, ToolSpec

DAY = date(2026, 11, 11)
TOOL_TOKEN = "tool-token-for-this-chat"


def request(message: str, *, day: date = DAY, conversation: str = "u1") -> ChatRequest:
    return ChatRequest(
        conversation=conversation, trading_date=day, message=message, tool_token=TOOL_TOKEN
    )


class ScriptedLlm:
    """Plays a fixed list of tool calls, then a final reply. Records what it was sent."""

    def __init__(
        self, calls: list[tuple[str, JsonDict]] | None = None, reply: str = "Okay."
    ) -> None:
        self.calls = list(calls or [])
        self.reply = reply
        self.seen: list[list[Any]] = []

    async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn:
        self.seen.append(list(messages))
        if self.calls:
            name, payload = self.calls.pop(0)
            call = ToolCall(id=f"c{len(self.calls)}", name=name, input=payload)
            return LlmTurn(text="", tool_calls=(call,), assistant_message={"role": "assistant"})
        return LlmTurn(text=self.reply, assistant_message={"role": "assistant", "text": self.reply})

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[str, str, bool]]) -> Any:
        return {"role": "user", "results": results}


# What core answers for each tool, on an open day with a live signal.
OPEN_DAY: dict[str, JsonDict] = {
    "get_day_status": {"state": "ACTIVE", "lock_reason": None, "net_pnl": "0", "trades_taken": 0},
    "get_plan": {
        "status": "LOCKED",
        "profit_target": "5000",
        "loss_limit": "3000",
        "max_trades": 3,
    },
    "get_current_signal": {
        "signal": {
            "id": "s1",
            "symbol": "NIFTY24500CE",
            "entry": "100",
            "stop": "90",
            "target": "120",
            "valid_till": "10:42",
            "why": "Closed beyond the opening range",
        }
    },
    "get_events": {"events": []},
    "propose_trade": {"shown_on_screen": {"id": "s1"}, "note": "Nothing is placed."},
    "exit_trade": {"refused": "There is no open trade."},
}

LOCKED_DAY: dict[str, JsonDict] = OPEN_DAY | {
    "get_day_status": {
        "state": "LOCKED",
        "lock_reason": "LOSS_LIMIT",
        "net_pnl": "-3000.00",
        "trades_taken": 2,
    },
    "propose_trade": {"refused": "Trading is locked until the next trading day."},
}

IN_TRADE: dict[str, JsonDict] = OPEN_DAY | {"exit_trade": {"exiting": "NIFTY24500CE"}}


@dataclass
class FakeCore:
    answers: dict[str, JsonDict] = field(default_factory=lambda: dict(OPEN_DAY))
    failing: set[str] = field(default_factory=set)
    calls: list[tuple[str, str, JsonDict]] = field(default_factory=list)

    async def call(self, token: str, name: str, payload: JsonDict) -> tuple[str, bool]:
        self.calls.append((token, name, payload))
        if name in self.failing:
            return json.dumps({"error": "Order rejected: price 77.25 is outside the band"}), True
        return json.dumps(self.answers.get(name, {"refused": f"Unknown tool {name}"})), False

    def called(self, name: str) -> bool:
        return any(call[1] == name for call in self.calls)
