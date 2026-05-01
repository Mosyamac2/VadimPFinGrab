"""Pydantic model coverage for prompt 02 schemas."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from edx.config import (
    AnthropicProviderConfig,
    AppConfig,
    AppMode,
    AppPaths,
    AppSchedule,
    AppSettings,
    EventTypesConfig,
    EventTypeSpec,
    LLMConfig,
    MetricsConfig,
    MetricSpec,
    OCRConfig,
    OpenRouterProviderConfig,
    Secrets,
    TickerEntry,
    TickersConfig,
)


def test_app_config_defaults_match_tz() -> None:
    cfg = AppConfig()
    assert cfg.schedule.cron_time == "04:00"
    assert cfg.mode.backfill_years == 3
    assert cfg.mode.default_run_mode == "update"
    assert cfg.paths.state_db.as_posix() == "data/state.sqlite"
    assert cfg.paths.excel_path.as_posix() == "output/e-disclosure.xlsx"


def test_app_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"unknown_section": True})


def test_app_mode_backfill_lower_bound() -> None:
    with pytest.raises(ValidationError):
        AppMode(backfill_years=0)


def test_app_paths_explicit_construction() -> None:
    paths = AppPaths(state_db="custom/state.db")  # type: ignore[arg-type]
    assert paths.state_db.name == "state.db"


def test_app_schedule_custom() -> None:
    s = AppSchedule(cron_time="05:30", timezone="UTC")
    assert s.cron_time == "05:30"
    assert s.timezone == "UTC"


def test_ticker_entry_minimal_required() -> None:
    t = TickerEntry(ticker="SBER", e_disclosure_id="123", name="Sberbank")
    assert t.inn is None
    assert t.priority_override is None


def test_ticker_entry_priority_override_validates() -> None:
    t = TickerEntry(
        ticker="X",
        e_disclosure_id="1",
        name="X",
        priority_override=["RSBU", "IFRS"],
    )
    assert t.priority_override == ["RSBU", "IFRS"]
    with pytest.raises(ValidationError):
        TickerEntry(
            ticker="X",
            e_disclosure_id="1",
            name="X",
            priority_override=["GAAP"],  # type: ignore[list-item]
        )


def test_tickers_config_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        TickersConfig.model_validate({"tickers": [], "junk": 1})


def test_metrics_config_invalid_priority_raises() -> None:
    with pytest.raises(ValidationError):
        MetricsConfig.model_validate(
            {"metrics": [], "reporting_priority": ["GAAP"]}
        )


def test_metrics_config_extra_field_raises() -> None:
    with pytest.raises(ValidationError):
        MetricsConfig.model_validate(
            {"metrics": [], "reporting_priority": ["IFRS"], "unknown": 1}
        )


def test_metric_spec_full_round_trip() -> None:
    spec = MetricSpec(
        canonical_name="revenue",
        synonyms_ifrs=["Revenue"],
        synonyms_rsbu=["Выручка"],
        unit="thousands",
        currency="USD",
        formula="x+y",
    )
    assert spec.formula == "x+y"
    assert spec.unit == "thousands"
    assert spec.currency == "USD"


def test_metric_spec_currency_length_strict() -> None:
    with pytest.raises(ValidationError):
        MetricSpec(canonical_name="x", currency="DOLLAR")


def test_event_types_config_requires_other_when_nonempty() -> None:
    with pytest.raises(ValidationError):
        EventTypesConfig.model_validate(
            {"event_types": [{"code": "dividends", "display_name": "Дивиденды"}]}
        )
    EventTypesConfig.model_validate({"event_types": []})  # empty is allowed


def test_event_type_spec_aliases_default_empty() -> None:
    spec = EventTypeSpec(code="other", display_name="Прочее")
    assert spec.aliases == []
    assert spec.description is None


def test_llm_config_defaults_use_claude_sonnet_46() -> None:
    cfg = LLMConfig()
    assert isinstance(cfg.primary, AnthropicProviderConfig)
    assert cfg.primary.model == "claude-sonnet-4-6"
    assert isinstance(cfg.fallback, OpenRouterProviderConfig)
    assert cfg.fallback.base_url.startswith("https://")
    assert cfg.max_retries == 3
    assert cfg.concurrency == 4


def test_llm_config_temperature_bounds() -> None:
    with pytest.raises(ValidationError):
        LLMConfig(temperature=-0.1)
    with pytest.raises(ValidationError):
        LLMConfig(temperature=2.5)


def test_ocr_config_defaults() -> None:
    cfg = OCRConfig()
    assert cfg.engine == "tesseract"
    assert cfg.tesseract_langs == ["rus", "eng"]
    assert cfg.tesseract_dpi == 300


def test_ocr_config_invalid_engine() -> None:
    with pytest.raises(ValidationError):
        OCRConfig.model_validate({"engine": "abbyy"})


def test_secrets_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-456")
    secrets = Secrets()
    assert isinstance(secrets.anthropic_api_key, SecretStr)
    assert secrets.anthropic_api_key.get_secret_value() == "sk-test-123"
    assert secrets.openrouter_api_key is not None
    assert secrets.openrouter_api_key.get_secret_value() == "sk-or-456"


def test_app_settings_to_masked_dict_hides_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key-DO-NOT-LEAK")
    settings = AppSettings(
        app=AppConfig(),
        tickers=TickersConfig(),
        metrics=MetricsConfig(metrics=[], reporting_priority=["IFRS", "RSBU"]),
        event_types=EventTypesConfig(),
        llm=LLMConfig(),
        ocr=OCRConfig(),
        secrets=Secrets(),
    )
    dumped = settings.to_masked_dict()
    assert dumped["secrets"]["anthropic_api_key"] == "***"
    # No raw secret material anywhere in the dump.
    text = repr(dumped)
    assert "sk-real-key" not in text
