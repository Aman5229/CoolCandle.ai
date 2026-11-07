"""The trade state machine: pure transitions, no I/O."""

from enum import StrEnum


class TradeState(StrEnum):
    PROPOSED = "PROPOSED"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN = "OPEN"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class TradeEvent(StrEnum):
    CONFIRM = "CONFIRM"
    EXPIRE = "EXPIRE"
    DECLINE = "DECLINE"
    FILL = "FILL"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    EXIT_TRIGGER = "EXIT_TRIGGER"
    EXIT_FILL = "EXIT_FILL"
    ANOMALY = "ANOMALY"
    RESOLVE = "RESOLVE"


class InvalidTransition(Exception):
    def __init__(self, state: TradeState, event: TradeEvent) -> None:
        super().__init__(f"{event} is not valid from {state}")


TRANSITIONS: dict[tuple[TradeState, TradeEvent], TradeState] = {
    (TradeState.PROPOSED, TradeEvent.CONFIRM): TradeState.ENTRY_PENDING,
    (TradeState.PROPOSED, TradeEvent.EXPIRE): TradeState.EXPIRED,
    (TradeState.PROPOSED, TradeEvent.DECLINE): TradeState.EXPIRED,
    (TradeState.ENTRY_PENDING, TradeEvent.FILL): TradeState.OPEN,
    (TradeState.ENTRY_PENDING, TradeEvent.REJECT): TradeState.REJECTED,
    (TradeState.ENTRY_PENDING, TradeEvent.CANCEL): TradeState.CANCELLED,
    (TradeState.ENTRY_PENDING, TradeEvent.ANOMALY): TradeState.NEEDS_ATTENTION,
    (TradeState.OPEN, TradeEvent.EXIT_TRIGGER): TradeState.EXITING,
    (TradeState.OPEN, TradeEvent.EXIT_FILL): TradeState.CLOSED,
    (TradeState.OPEN, TradeEvent.ANOMALY): TradeState.NEEDS_ATTENTION,
    (TradeState.EXITING, TradeEvent.EXIT_FILL): TradeState.CLOSED,
    (TradeState.EXITING, TradeEvent.ANOMALY): TradeState.NEEDS_ATTENTION,
    (TradeState.NEEDS_ATTENTION, TradeEvent.EXIT_TRIGGER): TradeState.EXITING,
    (TradeState.NEEDS_ATTENTION, TradeEvent.EXIT_FILL): TradeState.CLOSED,
    (TradeState.NEEDS_ATTENTION, TradeEvent.RESOLVE): TradeState.OPEN,
    (TradeState.NEEDS_ATTENTION, TradeEvent.CANCEL): TradeState.CANCELLED,
}

# States that still hold, or may still become, a position.
ACTIVE_STATES = frozenset(
    {
        TradeState.ENTRY_PENDING,
        TradeState.OPEN,
        TradeState.EXITING,
        TradeState.NEEDS_ATTENTION,
    }
)


def next_state(state: TradeState, event: TradeEvent) -> TradeState:
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise InvalidTransition(state, event) from None
