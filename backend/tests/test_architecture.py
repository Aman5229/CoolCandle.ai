"""Rules the type checker can't see. The source itself is the test subject."""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "coolcandle"
ORDER_METHODS = {"place_order", "modify_order", "cancel_order"}
THE_ONE_DOOR = SRC / "services" / "orders" / "service.py"
# Skill Test's door: it only ever holds the PaperBroker built for its own session, and
# import-linter keeps it away from live trading's brokers and services.
SKILL_DOOR = SRC / "services" / "skill" / "arena.py"


def python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "migrations" not in p.parts]


def calls_to(path: Path, names: set[str]) -> list[int]:
    tree = ast.parse(path.read_text())
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in names
    ]


def test_only_order_service_calls_the_broker_order_methods() -> None:
    offenders = []
    for path in python_files():
        if path in (THE_ONE_DOOR, SKILL_DOOR) or "adapters" in path.parts:
            continue
        if calls_to(path, ORDER_METHODS):
            offenders.append(str(path))
    assert offenders == []


def test_the_one_door_does_call_them() -> None:
    assert calls_to(THE_ONE_DOOR, ORDER_METHODS)


def test_skill_test_builds_its_broker_only_from_its_own_factory() -> None:
    """The skill door never names a broker class or the router: it gets one PaperBroker per
    session from the factory main.py hands it."""
    text = SKILL_DOOR.read_text()
    for name in ("BrokerRouter", "KotakBroker", "PaperBroker(", "brokers.get("):
        assert name not in text, name


def test_the_domain_never_touches_the_clock() -> None:
    offenders = []
    for path in (SRC / "domain").rglob("*.py"):
        if calls_to(path, {"now", "utcnow", "today"}):
            offenders.append(str(path))
    assert offenders == []


def test_money_is_never_a_float_in_the_domain() -> None:
    offenders = []
    for path in (SRC / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "float":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_no_code_deletes_a_day_lock() -> None:
    for path in python_files():
        text = path.read_text()
        assert "delete(DayLock" not in text, path


def test_the_agent_has_no_tool_that_can_enter_a_trade() -> None:
    from coolcandle.services.agent.tools import TOOL_NAMES

    assert TOOL_NAMES == {
        "get_day_status",
        "get_plan",
        "get_current_signal",
        "get_events",
        "propose_trade",
        "exit_trade",
    }


def test_the_agent_tools_are_not_reachable_from_the_internet() -> None:
    """Caddy forwards /api and /ws to core and nothing else, so /internal stays private."""
    caddyfile = (SRC.parent.parent.parent / "deploy" / "Caddyfile").read_text()
    forwarded = [
        line.split()[1] for line in caddyfile.splitlines() if line.strip().startswith("handle /")
    ]
    assert forwarded == ["/api/*", "/ws"]
