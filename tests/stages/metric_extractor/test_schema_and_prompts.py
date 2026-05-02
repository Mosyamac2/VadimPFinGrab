"""Tests for the (profile, source_standard)-shaped JSON schema and prompt."""

from __future__ import annotations

import json

from edx.config import MetricsConfig, MetricSpec, MetricsProfile
from edx.stages.metric_extractor.prompts import build_system_prompt
from edx.stages.metric_extractor.schema import build_metric_extraction_schema


def _config() -> MetricsConfig:
    return MetricsConfig(
        profiles={
            "non_bank": MetricsProfile(
                metrics={
                    "revenue": MetricSpec(
                        synonyms=["Revenue", "Выручка"],
                        scale_hints=["млн руб."],
                    ),
                    "ebitda": MetricSpec(
                        synonyms=["EBITDA"],
                        only_in_sources=["IFRS", "ISSUER"],
                    ),
                    "total_debt": MetricSpec(
                        synonyms=["Заемные средства"],
                        aggregation_hint="sum 1410+1510",
                    ),
                },
                reporting_priority=["IFRS", "RSBU", "ISSUER"],
            ),
            "bank": MetricsProfile(
                metrics={
                    "net_interest_income": MetricSpec(
                        synonyms=[
                            "Чистый процентный доход",
                            "Чистые процентные доходы",
                        ]
                    ),
                },
                reporting_priority=["IFRS", "RSBU", "ISSUER"],
            ),
        }
    )


# --- schema ----------------------------------------------------------------


def test_schema_top_level_shape() -> None:
    profile = _config().for_profile("non_bank")
    schema = build_metric_extraction_schema(profile, source_standard="IFRS")
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["extractions"]
    assert schema["properties"]["extractions"]["type"] == "array"


def test_schema_metric_block_required_matches_profile_for_ifrs() -> None:
    profile = _config().for_profile("non_bank")
    schema = build_metric_extraction_schema(profile, source_standard="IFRS")
    metric_block = schema["properties"]["extractions"]["items"]["properties"][
        "metrics"
    ]
    assert metric_block["additionalProperties"] is False
    # ebitda included for IFRS, total_debt always present.
    assert set(metric_block["required"]) == {"revenue", "ebitda", "total_debt"}


def test_schema_drops_only_in_sources_metrics_for_rsbu() -> None:
    """Patch 19: ebitda is IFRS/ISSUER-only — it must not appear in the
    RSBU schema (no temptation for the LLM to fabricate one)."""
    profile = _config().for_profile("non_bank")
    schema = build_metric_extraction_schema(profile, source_standard="RSBU")
    metric_block = schema["properties"]["extractions"]["items"]["properties"][
        "metrics"
    ]
    assert "ebitda" not in metric_block["properties"]
    assert "ebitda" not in metric_block["required"]
    assert {"revenue", "total_debt"}.issubset(set(metric_block["required"]))


def test_schema_period_enums_pinned() -> None:
    profile = _config().for_profile("non_bank")
    schema = build_metric_extraction_schema(profile, source_standard="IFRS")
    period = schema["properties"]["extractions"]["items"]["properties"]
    assert period["period_type"]["enum"] == [
        "Q1", "Q2", "Q3", "Q4", "H1", "H2", "9M", "FY",
    ]
    assert period["reporting_standard"]["enum"] == ["IFRS", "RSBU", "ISSUER"]
    assert period["unit"]["enum"] == [
        "ones", "thousands", "millions", "billions",
    ]


def test_schema_is_serialisable_to_json() -> None:
    profile = _config().for_profile("bank")
    schema = build_metric_extraction_schema(profile, source_standard="IFRS")
    blob = json.dumps(schema, sort_keys=True)
    parsed = json.loads(blob)
    assert parsed == schema


# --- prompt ----------------------------------------------------------------


def test_prompt_contains_synonyms_and_priority() -> None:
    profile = _config().for_profile("non_bank")
    prompt = build_system_prompt(profile, source_standard="IFRS")
    assert "Revenue" in prompt
    assert "Выручка" in prompt
    assert "EBITDA" in prompt
    assert "IFRS > RSBU > ISSUER" in prompt
    assert "Не выдумывай" in prompt


def test_prompt_drops_only_in_sources_metric_for_rsbu() -> None:
    profile = _config().for_profile("non_bank")
    prompt = build_system_prompt(profile, source_standard="RSBU")
    # ebitda is IFRS/ISSUER-only — its line and synonyms vanish for RSBU.
    assert "ebitda" not in prompt.lower()


def test_prompt_includes_aggregation_hint_only_for_rsbu() -> None:
    profile = _config().for_profile("non_bank")
    rsbu_prompt = build_system_prompt(profile, source_standard="RSBU")
    ifrs_prompt = build_system_prompt(profile, source_standard="IFRS")
    assert "1410+1510" in rsbu_prompt
    assert "sum 1410+1510" in rsbu_prompt
    assert "1410+1510" not in ifrs_prompt


def test_prompt_for_bank_profile_carries_bank_synonyms_only() -> None:
    profile = _config().for_profile("bank")
    prompt = build_system_prompt(profile, source_standard="IFRS")
    assert "Чистый процентный доход" in prompt
    assert "Чистые процентные доходы" in prompt
    # Bank prompt does NOT mention non-bank revenue / EBITDA.
    assert "Revenue" not in prompt
    assert "EBITDA" not in prompt


def test_prompt_is_deterministic() -> None:
    profile = _config().for_profile("non_bank")
    a = build_system_prompt(profile, source_standard="IFRS")
    b = build_system_prompt(profile, source_standard="IFRS")
    assert a == b
