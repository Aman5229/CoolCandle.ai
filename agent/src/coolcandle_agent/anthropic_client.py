"""The buddy's model: Claude through the official SDK."""

from typing import Any

import anthropic

from coolcandle_agent.llm import JsonDict, LlmTurn, ToolCall, ToolSpec

MAX_TOKENS = 16000


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def turn(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> LlmTurn:
        definitions: list[JsonDict] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "strict": True,
            }
            for tool in tools
        ]
        create: Any = self.client.beta.messages.create
        response = await create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=definitions,
            messages=messages,
            output_config={"effort": "low"},  # a chat turn has to feel quick
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return LlmTurn(text="", refused=True)
        text = "".join(block.text for block in response.content if block.type == "text")
        calls = tuple(
            ToolCall(id=block.id, name=block.name, input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        )
        # The full content goes back unchanged, because it can hold thinking blocks.
        return LlmTurn(
            text=text,
            tool_calls=calls,
            assistant_message={"role": "assistant", "content": response.content},
        )

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[str, str, bool]]) -> Any:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": failed}
                for call_id, text, failed in results
            ],
        }
