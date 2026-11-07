"""Charges estimate for index options. Rates are configuration, never constants in logic."""

from decimal import ROUND_HALF_UP, Decimal

from coolcandle.domain.models import Frozen

PAISA = Decimal("0.01")


class CostRates(Frozen):
    brokerage_per_order: Decimal = Decimal("10")
    stt_sell_pct: Decimal = Decimal("0.15")
    exchange_pct: Decimal = Decimal("0.03503")
    sebi_pct: Decimal = Decimal("0.0001")
    stamp_buy_pct: Decimal = Decimal("0.003")
    gst_pct: Decimal = Decimal("18")


def money(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def estimate(
    buy_value: Decimal, sell_value: Decimal, executed_orders: int, rates: CostRates
) -> Decimal:
    """Total charges for a set of fills."""
    turnover = buy_value + sell_value
    brokerage = rates.brokerage_per_order * executed_orders
    stt = sell_value * rates.stt_sell_pct / 100
    exchange = turnover * rates.exchange_pct / 100
    sebi = turnover * rates.sebi_pct / 100
    stamp = buy_value * rates.stamp_buy_pct / 100
    gst = (brokerage + exchange + sebi) * rates.gst_pct / 100
    return money(brokerage + stt + exchange + sebi + stamp + gst)


def round_trip(entry: Decimal, exit_price: Decimal, qty: int, rates: CostRates) -> Decimal:
    return estimate(entry * qty, exit_price * qty, 2, rates)
