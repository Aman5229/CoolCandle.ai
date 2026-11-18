"""A run is one fake fund from start until it is retired or blown."""

from decimal import Decimal

from coolcandle.domain.plan import PlanError
from coolcandle.domain.skill.models import START_FUNDS, SkillSettings


def check_start_fund(amount: Decimal) -> Decimal:
    if amount not in START_FUNDS:
        choices = ", ".join(f"₹{int(fund):,}" for fund in START_FUNDS)
        raise PlanError("START_FUND", f"Start with one of {choices}")
    return amount


def blown_line(start_fund: Decimal, settings: SkillSettings) -> Decimal:
    """The equity at which the run ends: 30% below where it started, by default."""
    return start_fund * (100 - settings.blown_pct) / 100


def is_blown(equity: Decimal, start_fund: Decimal, settings: SkillSettings) -> bool:
    return equity < blown_line(start_fund, settings)
