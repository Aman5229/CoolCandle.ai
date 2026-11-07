"""Settings from the environment, prefix COOLCANDLE_."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COOLCANDLE_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+asyncpg://coolcandle:coolcandle@localhost:5432/coolcandle"
    secret_key: SecretStr
    allowed_origin: str = "http://localhost:5173"
    cookie_secure: bool = True
    public_url: str = "http://localhost:5173"
    data_dir: Path = Path("data")

    # sim: simulated prices and clock for local runs. kotak: the real feed after broker connect.
    market_source: Literal["sim", "kotak"] = "sim"
    sim_speed: float = 1.0
    sim_date: date | None = None  # a lock holds for its whole date, so a new day needs a new date
    sim_seed: int = 19

    live_order_cap: int = 30
    paper_cash: Decimal = Decimal("500000")
    # Trailing may only act on live trades after Kotak confirms automatic exits are allowed.
    trailing_live_enabled: bool = False
    signals_enabled: bool = True

    # Charges: verify against the current rate cards before going live.
    brokerage_per_order: Decimal = Decimal("10")
    stt_sell_pct: Decimal = Decimal("0.15")
    exchange_pct: Decimal = Decimal("0.03503")
    sebi_pct: Decimal = Decimal("0.0001")
    stamp_buy_pct: Decimal = Decimal("0.003")
    gst_pct: Decimal = Decimal("18")

    # The buddy's own service (ADR 0004). Without it the buddy says it is not set up.
    agent_url: str | None = None
    agent_token: SecretStr | None = None  # shared: the agent service refuses chats without it

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    news_rss_urls: list[str] = []

    @model_validator(mode="after")
    def _agent_needs_both(self) -> "Settings":
        if (self.agent_url is None) != (self.agent_token is None):
            raise ValueError("Set COOLCANDLE_AGENT_URL and COOLCANDLE_AGENT_TOKEN together")
        return self
