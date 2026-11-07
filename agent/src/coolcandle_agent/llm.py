"""The chat model behind the buddy. History is opaque: only the adapter reads it."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

JsonDict = dict[str, Any]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ToolSpec(_Frozen):
    name: str
    description: str
    input_schema: JsonDict


class ToolCall(_Frozen):
    id: str
    name: str
    input: JsonDict


class LlmTurn(_Frozen):
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    assistant_message: Any = None  # append to history unchanged
    refused: bool = False


class LlmClient(Protocol):
    async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn: ...

    def user_message(self, text: str) -> Any: ...
    def tool_results(self, results: list[tuple[str, str, bool]]) -> Any: ...
