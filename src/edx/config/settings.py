"""Aggregate ``AppSettings`` injected as a single object into every stage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from edx.config.app_config import AppConfig
from edx.config.event_types_config import EventTypesConfig
from edx.config.llm_config import LLMConfig
from edx.config.metrics_config import MetricsConfig
from edx.config.ocr_config import OCRConfig
from edx.config.secrets import Secrets
from edx.config.tickers_config import TickersConfig

SECRET_MASK = "***"


class AppSettings(BaseModel):
    """Top-level DI container — the only thing pipeline stages should consume."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    app: AppConfig
    tickers: TickersConfig
    metrics: MetricsConfig
    event_types: EventTypesConfig
    llm: LLMConfig
    ocr: OCRConfig
    secrets: Secrets = Field(default_factory=Secrets)

    def to_masked_dict(self) -> dict[str, Any]:
        """Render settings safe to print (secrets replaced with ``***``).

        Uses ``mode="json"`` so values are pure builtins (Path → str,
        SecretStr → masked) and safe for YAML/JSON serialisation.
        """
        raw = self.model_dump(mode="json")
        secrets_section = raw.get("secrets") or {}
        raw["secrets"] = {
            field_name: (SECRET_MASK if value is not None else None)
            for field_name, value in secrets_section.items()
        }
        return raw
