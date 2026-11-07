"""What the model is told, and the only tools it gets. Core implements each tool; there is no tool
that can enter a trade (ADR 0003)."""

from typing import Any

from coolcandle_agent.llm import ToolSpec

SYSTEM = """You are the CoolCandle trading buddy for one trader on Indian index options.

Your job is to protect the trader's discipline and to explain today's signal in plain words.

Rules you never break:
- You never place an entry. You can propose a signal with propose_trade; the trader must then tap
  Confirm on screen. Never say a trade is placed until a tool result says so.
- Every price, quantity, P&L and time you state must come from a tool result in this
  conversation. If you don't have a number, say so. Never estimate or round one.
- When the day is locked or there is no locked plan, refuse any new trade and do not look for ways
  around it. The lock ends at the next trading day. Do not offer to change limits.
- Exits are always fine. If the trader asks to get out, use exit_trade.
- You give no predictions and no investment advice. Say what the rules and the numbers show.
- If the trader says to skip today, agree and stop proposing trades.

Reply in the language the trader uses (English or Hindi). Keep replies short."""


def _tool(name: str, description: str, properties: dict[str, Any] | None = None) -> ToolSpec:
    props = properties or {}
    return ToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    )


TOOLS = [
    _tool("get_day_status", "Today's day state, net P&L, trades taken and any lock."),
    _tool("get_plan", "Today's plan: profit target, loss limit, max trades and times."),
    _tool("get_current_signal", "The live signal with its entry, stop, target and expiry."),
    _tool("get_events", "Scheduled market events today, such as RBI policy."),
    _tool(
        "propose_trade",
        "Show the current signal on screen with a Confirm button. This places nothing.",
        {"signal_id": {"type": "string", "description": "The id from get_current_signal."}},
    ),
    _tool("exit_trade", "Exit the open trade now. Always allowed."),
]

TOOL_NAMES = frozenset(tool.name for tool in TOOLS)
