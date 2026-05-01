"""Pydantic-validated configuration for the e-disclosure extractor."""

from edx.config.app_config import (
    AppConfig,
    AppMode,
    AppPaths,
    AppSchedule,
    DiscovererConfig,
)
from edx.config.event_types_config import EventTypesConfig, EventTypeSpec
from edx.config.llm_config import (
    AnthropicProviderConfig,
    LLMConfig,
    OpenRouterProviderConfig,
)
from edx.config.loader import ConfigLoadError, load_all
from edx.config.metrics_config import MetricsConfig, MetricSpec, ReportingStandard
from edx.config.ocr_config import OCRConfig
from edx.config.secrets import Secrets
from edx.config.settings import AppSettings
from edx.config.tickers_config import TickerEntry, TickersConfig

__all__ = [
    "AnthropicProviderConfig",
    "AppConfig",
    "AppMode",
    "AppPaths",
    "AppSchedule",
    "AppSettings",
    "ConfigLoadError",
    "DiscovererConfig",
    "EventTypeSpec",
    "EventTypesConfig",
    "LLMConfig",
    "MetricSpec",
    "MetricsConfig",
    "OCRConfig",
    "OpenRouterProviderConfig",
    "ReportingStandard",
    "Secrets",
    "TickerEntry",
    "TickersConfig",
    "load_all",
]
