"""Snapshot-style tests for the JSON schema and the system prompt."""

from __future__ import annotations

import json

from edx.config import MetricsConfig, MetricSpec
from edx.stages.metric_extractor.prompts import build_system_prompt
from edx.stages.metric_extractor.schema import build_metric_extraction_schema

_FIXTURE_CONFIG = MetricsConfig(
    metrics=[
        MetricSpec(
            canonical_name="revenue",
            synonyms_ifrs=["Revenue", "Total revenue"],
            synonyms_rsbu=["Выручка"],
            unit="ones",
            currency="RUB",
        ),
        MetricSpec(
            canonical_name="ebitda",
            synonyms_ifrs=["EBITDA"],
            synonyms_rsbu=[],
            unit="ones",
            currency="RUB",
            formula="net_income + depreciation",
        ),
    ],
    reporting_priority=["IFRS", "RSBU"],
)


def test_build_schema_top_level_shape() -> None:
    schema = build_metric_extraction_schema(_FIXTURE_CONFIG)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["extractions"]
    assert schema["properties"]["extractions"]["type"] == "array"


def test_build_schema_metric_block_required_keys_match_config() -> None:
    schema = build_metric_extraction_schema(_FIXTURE_CONFIG)
    period = schema["properties"]["extractions"]["items"]
    metric_block = period["properties"]["metrics"]
    assert metric_block["additionalProperties"] is False
    assert metric_block["required"] == ["revenue", "ebitda"]
    rev = metric_block["properties"]["revenue"]
    assert rev["additionalProperties"] is False
    assert rev["properties"]["value"]["type"] == ["number", "null"]
    assert rev["properties"]["source_quote"]["type"] == ["string", "null"]


def test_schema_period_enums_pinned() -> None:
    schema = build_metric_extraction_schema(_FIXTURE_CONFIG)
    period = schema["properties"]["extractions"]["items"]["properties"]
    assert period["period_type"]["enum"] == [
        "Q1", "Q2", "Q3", "Q4", "H1", "H2", "9M", "FY",
    ]
    assert period["reporting_standard"]["enum"] == ["IFRS", "RSBU"]
    assert period["unit"]["enum"] == [
        "ones", "thousands", "millions", "billions",
    ]


def test_schema_is_serialisable_to_json() -> None:
    schema = build_metric_extraction_schema(_FIXTURE_CONFIG)
    blob = json.dumps(schema, sort_keys=True)
    # round-trip
    parsed = json.loads(blob)
    assert parsed == schema


def test_build_system_prompt_contains_synonyms_and_priority() -> None:
    prompt = build_system_prompt(_FIXTURE_CONFIG)
    assert "Revenue" in prompt
    assert "Выручка" in prompt
    assert "EBITDA" in prompt
    assert "Приоритет стандартов отчётности" in prompt
    assert "IFRS > RSBU" in prompt
    assert "Не выдумывай" in prompt
    # Formula is surfaced for the operator/LLM context.
    assert "net_income + depreciation" in prompt


def test_build_system_prompt_is_deterministic() -> None:
    a = build_system_prompt(_FIXTURE_CONFIG)
    b = build_system_prompt(_FIXTURE_CONFIG)
    assert a == b
