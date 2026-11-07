from coolcandle.ports.broker import BrokerSpec, BrokerStatus, FieldSpec

KOTAK_SPEC = BrokerSpec(
    id="kotak_neo",
    name="Kotak Neo",
    status=BrokerStatus.LIVE,
    fields=(
        FieldSpec(name="consumer_key", label="Consumer key", secret=True),
        FieldSpec(name="mobile_number", label="Mobile number (+91...)"),
        FieldSpec(name="ucc", label="UCC"),
        FieldSpec(name="mpin", label="MPIN", type="password", secret=True),
    ),
    daily_fields=(FieldSpec(name="totp", label="Kotak TOTP", secret=True),),
)
