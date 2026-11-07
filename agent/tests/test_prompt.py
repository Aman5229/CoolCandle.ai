from coolcandle_agent.prompt import SYSTEM, TOOL_NAMES, TOOLS


def test_the_agent_has_no_tool_that_can_enter_a_trade() -> None:
    """The same six names as core's TOOL_NAMES. Core implements nothing else."""
    assert TOOL_NAMES == {
        "get_day_status",
        "get_plan",
        "get_current_signal",
        "get_events",
        "propose_trade",
        "exit_trade",
    }


def test_every_tool_takes_only_what_it_declares() -> None:
    for tool in TOOLS:
        assert tool.input_schema["additionalProperties"] is False
        assert tool.input_schema["required"] == list(tool.input_schema["properties"])


def test_the_prompt_states_the_rules_that_matter() -> None:
    for rule in (
        "You never place an entry",
        "must come from a tool result",
        "refuse any new trade",
        "Exits are always fine",
        "no predictions and no investment advice",
    ):
        assert rule in SYSTEM
