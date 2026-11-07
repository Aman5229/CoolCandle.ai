"""The buddy's tools. They run here, in core, because they read the guard and can exit a trade.
There is no tool that can enter a trade (ADR 0003): only a tap on screen can."""

from typing import Any

from coolcandle.domain.calendar import MarketCalendar, to_ist
from coolcandle.domain.guard import DayState
from coolcandle.domain.models import ExitReason, JsonDict
from coolcandle.domain.signals import Signal
from coolcandle.ports.clock import Clock
from coolcandle.services.auth.service import CurrentUser
from coolcandle.services.events.service import EventService
from coolcandle.services.guard.service import GuardService
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.service import OrderService
from coolcandle.services.signals.service import SignalService

TOOL_NAMES = frozenset(
    {
        "get_day_status",
        "get_plan",
        "get_current_signal",
        "get_events",
        "propose_trade",
        "exit_trade",
    }
)


def _signal_facts(signal: Signal) -> JsonDict:
    return {
        "id": signal.id,
        "symbol": signal.trading_symbol,
        "entry": str(signal.entry),
        "stop": str(signal.stop),
        "target": str(signal.target),
        "valid_till": f"{to_ist(signal.expires_at):%H:%M}",
        "why": signal.reason,
    }


class AgentTools:
    def __init__(
        self,
        *,
        clock: Clock,
        calendar: MarketCalendar,
        guard: GuardService,
        signals: SignalService,
        orders: OrderService,
        book: TradeBook,
        events: EventService,
    ) -> None:
        self.clock = clock
        self.calendar = calendar
        self.guard = guard
        self.signals = signals
        self.orders = orders
        self.book = book
        self.events = events

    async def _day_is_open(self, user: CurrentUser) -> tuple[bool, str]:
        view = await self.guard.view(user.account_id, user.mode)
        state = view.status.state
        if state is DayState.LOCKED:
            return False, "Trading is locked until the next trading day."
        if state is DayState.NO_PLAN:
            return False, "There is no locked plan today."
        return True, ""

    async def run(
        self, user: CurrentUser, name: str, payload: dict[str, Any]
    ) -> tuple[JsonDict, Signal | None]:
        """The tool's result for the model, and the signal it put on screen, if any.
        A TradeError from an exit propagates to the caller."""
        proposal = None
        result: JsonDict
        if name not in TOOL_NAMES:
            result = {"refused": f"Unknown tool {name}"}
        elif name == "get_day_status":
            view = await self.guard.view(user.account_id, user.mode)
            snap = view.snapshot
            result = {
                "state": view.status.state,
                "lock_reason": view.status.lock_reason,
                "net_pnl": str(snap.net_pnl),
                "trades_taken": snap.trades_taken,
                "open_trade": snap.open_trade,
                "reasons": [r.message for r in view.status.reasons],
            }
        elif name == "get_plan":
            plan = (await self.guard.view(user.account_id, user.mode)).plan
            result = (
                {"plan": None}
                if plan is None
                else {
                    "status": plan.status,
                    "profit_target": str(plan.profit_target),
                    "loss_limit": str(plan.loss_limit),
                    "max_trades": plan.max_trades,
                    "max_loss_per_trade": str(plan.max_loss_per_trade),
                    "entry_cutoff": f"{plan.entry_cutoff:%H:%M}",
                    "square_off": f"{plan.square_off:%H:%M}",
                }
            )
        elif name == "get_current_signal":
            signal = await self.signals.current(user.account_id)
            result = {"signal": _signal_facts(signal) if signal else None}
        elif name == "get_events":
            today = self.calendar.trading_date(self.clock.now())
            result = {
                "events": [
                    {"name": e.name, "at": f"{e.at:%H:%M}" if e.at else None}
                    for e in self.events.on(today)
                ]
            }
        elif name == "propose_trade":
            is_open, why = await self._day_is_open(user)
            if not is_open:
                result = {"refused": why}
            else:
                signal = await self.signals.current(user.account_id)
                if signal is None or signal.id != payload.get("signal_id"):
                    result = {"refused": "That signal is no longer live."}
                else:
                    proposal = signal
                    result = {
                        "shown_on_screen": _signal_facts(signal),
                        "note": "Nothing is placed. The trader must tap Confirm.",
                    }
        else:  # exit_trade
            active = self.book.active(user.account_id, user.mode)
            if not active:
                result = {"refused": "There is no open trade."}
            else:
                trade = await self.orders.exit_trade(active[0].id, ExitReason.MANUAL)
                result = {"exiting": trade.trading_symbol}
        return result, proposal
