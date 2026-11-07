"""What core sends and what comes back. Core's side is `coolcandle.ports.agent`; ADR 0004 holds
both, and each service tests its half against the same example."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation: str  # one per trader
    trading_date: date  # a new trading day starts a new conversation
    message: str
    tool_token: str  # for core's tools, valid only while this request runs


class ChatAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
