"""Settings from the environment, prefix COOLCANDLE_AGENT_."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COOLCANDLE_AGENT_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    # Core sends it with every chat: the same value as core's COOLCANDLE_AGENT_TOKEN.
    token: SecretStr
    core_url: str = "http://localhost:8000"
    anthropic_api_key: SecretStr | None = None
    model: str = "claude-opus-5-5"

    @field_validator("token")
    @classmethod
    def _long_enough(cls, token: SecretStr) -> SecretStr:
        if len(token.get_secret_value()) < 32:
            raise ValueError("needs at least 32 characters: use secrets.token_urlsafe(32)")
        return token
