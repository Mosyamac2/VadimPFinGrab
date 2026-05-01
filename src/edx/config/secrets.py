"""Secrets sourced from ``.env`` via Pydantic Settings (ТЗ §13)."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """Holds API keys and OAuth tokens. Never log raw values."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    google_oauth_client_id: SecretStr | None = None
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_refresh_token: SecretStr | None = None
    yandex_vision_ocr_key: SecretStr | None = None

    @classmethod
    def load(cls, env_file: Path | None = None) -> Secrets:
        """Load secrets, optionally pointing at a non-default ``.env`` file."""
        if env_file is None:
            return cls()
        return cls(_env_file=env_file)  # type: ignore[call-arg]
