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
    MetricsProfile,
    OCRConfig,
    OpenRouterProviderConfig,
    Secrets,
    TickerEntry,
    TickersConfig,
)


def _two_profile_metrics_config() -> MetricsConfig:
    """Patch 19: minimal MetricsConfig that satisfies the both-profiles
    invariant. Used by tests that need a valid object without exercising
    the metric catalogue."""
    return MetricsConfig(
        profiles={
            "non_bank": MetricsProfile(
                metrics={"revenue": MetricSpec(synonyms=["Выручка"])},
                reporting_priority=["IFRS", "RSBU"],
            ),
            "bank": MetricsProfile(
                metrics={
                    "net_interest_income": MetricSpec(
                        synonyms=["Чистый процентный доход"]
                    )
                },
                reporting_priority=["IFRS", "RSBU"],
            ),
        }
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


def test_metrics_config_rejects_legacy_flat_schema() -> None:
    """Patch 19: pre-Patch-19 ``metrics:`` / ``reporting_priority:`` at the
    top level fails fast with a clear message instead of silently dropping
    metrics."""
    with pytest.raises(ValidationError) as exc_info:
        MetricsConfig.model_validate(
            {"metrics": [], "reporting_priority": ["IFRS"]}
        )
    assert "pre-Patch-19" in str(exc_info.value) or "profiles" in str(
        exc_info.value
    )


def test_metrics_config_requires_both_profiles() -> None:
    with pytest.raises(ValidationError):
        MetricsConfig.model_validate(
            {
                "profiles": {
                    "non_bank": {
                        "metrics": {"revenue": {"synonyms": ["Выручка"]}},
                        "reporting_priority": ["IFRS"],
                    }
                }
            }
        )


def test_metrics_config_extra_field_raises() -> None:
    with pytest.raises(ValidationError):
        MetricsConfig.model_validate(
            {
                "profiles": {
                    "non_bank": {
                        "metrics": {"revenue": {"synonyms": ["Выручка"]}},
                        "reporting_priority": ["IFRS"],
                    },
                    "bank": {
                        "metrics": {"net_income": {"synonyms": ["Прибыль"]}},
                        "reporting_priority": ["IFRS"],
                    },
                },
                "unknown": 1,
            }
        )


def test_metric_spec_full_round_trip() -> None:
    spec = MetricSpec(
        synonyms=["Revenue", "Выручка"],
        unit="RUB",
        scale_hints=["млн руб.", "тыс. руб."],
        only_in_sources=["IFRS", "ISSUER"],
        aggregation_hint="sum 1410+1510",
    )
    assert spec.synonyms == ["Revenue", "Выручка"]
    assert spec.unit == "RUB"
    assert spec.only_in_sources == ["IFRS", "ISSUER"]
    assert spec.aggregation_hint == "sum 1410+1510"


def test_metric_spec_requires_at_least_one_synonym() -> None:
    with pytest.raises(ValidationError):
        MetricSpec(synonyms=[])


def test_metrics_config_for_profile_returns_profile() -> None:
    cfg = _two_profile_metrics_config()
    assert "revenue" in cfg.for_profile("non_bank").metrics
    assert "net_interest_income" in cfg.for_profile("bank").metrics


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
        metrics=_two_profile_metrics_config(),
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
