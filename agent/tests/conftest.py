import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--agent-evals", action="store_true", help="Run evals on the real model")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_model: spends real money on the model API")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--agent-evals"):
        return
    skip = pytest.mark.skip(reason="needs --agent-evals and an API key")
    for item in items:
        if "real_model" in item.keywords:
            item.add_marker(skip)
