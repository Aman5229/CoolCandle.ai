"""The brokers CoolCandle knows. The connect form is built from these fields."""

from coolcandle.adapters.brokers.kotak_neo.spec import KOTAK_SPEC
from coolcandle.ports.broker import BrokerSpec, BrokerStatus, FieldSpec

_API_KEY = FieldSpec(name="api_key", label="API key", secret=True)
_API_SECRET = FieldSpec(name="api_secret", label="API secret", secret=True)

CATALOG: tuple[BrokerSpec, ...] = (
    KOTAK_SPEC,
    BrokerSpec(
        id="zerodha",
        name="Zerodha (Kite Connect)",
        status=BrokerStatus.COMING_SOON,
        fields=(_API_KEY, _API_SECRET),
    ),
    BrokerSpec(
        id="dhan",
        name="Dhan",
        status=BrokerStatus.COMING_SOON,
        fields=(
            FieldSpec(name="client_id", label="Client ID"),
            FieldSpec(name="access_token", label="Access token", secret=True),
        ),
    ),
    BrokerSpec(
        id="angel_one",
        name="Angel One (SmartAPI)",
        status=BrokerStatus.COMING_SOON,
        fields=(
            _API_KEY,
            FieldSpec(name="client_code", label="Client code"),
            FieldSpec(name="pin", label="PIN", type="password", secret=True),
        ),
    ),
    BrokerSpec(
        id="upstox",
        name="Upstox",
        status=BrokerStatus.COMING_SOON,
        fields=(_API_KEY, _API_SECRET),
    ),
)
