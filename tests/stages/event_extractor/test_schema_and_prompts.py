"""Schema + system prompt deterministic tests."""

from __future__ import annotations

from edx.config import EventTypesConfig, EventTypeSpec
from edx.stages.event_extractor.prompts import build_system_prompt
from edx.stages.event_extractor.schema import build_event_extraction_schema

_FIXTURE_TYPES = EventTypesConfig(
    event_types=[
        EventTypeSpec(
            code="dividends",
            display_name="Дивиденды",
            aliases=["Объявление дивидендов"],
        ),
        EventTypeSpec(
            code="management_change",
            display_name="Смена менеджмента",
        ),
        EventTypeSpec(
            code="other",
            display_name="Прочее",
            description="Резервный код, если ничего не подошло.",
        ),
    ]
)


def test_schema_top_level_shape() -> None:
    schema = build_event_extraction_schema(_FIXTURE_TYPES)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == [
        "event_date",
        "event_type",
        "key_params",
        "publication_date",
        "summary",
    ]


def test_event_type_enum_pinned_to_config_codes() -> None:
    schema = build_event_extraction_schema(_FIXTURE_TYPES)
    enum = schema["properties"]["event_type"]["enum"]
    assert enum == ["dividends", "management_change", "other"]


def test_summary_max_length_enforced_in_schema() -> None:
    schema = build_event_extraction_schema(_FIXTURE_TYPES)
    assert schema["properties"]["summary"]["maxLength"] == 600


def test_key_params_allows_primitive_values() -> None:
    schema = build_event_extraction_schema(_FIXTURE_TYPES)
    additional = schema["properties"]["key_params"]["additionalProperties"]
    assert sorted(additional["type"]) == ["boolean", "null", "number", "string"]


def test_dates_nullable_with_iso_pattern() -> None:
    schema = build_event_extraction_schema(_FIXTURE_TYPES)
    for field in ("event_date", "publication_date"):
        prop = schema["properties"][field]
        assert prop["type"] == ["string", "null"]
        assert prop["pattern"].startswith(r"^\d{4}")


def test_system_prompt_lists_codes_aliases_and_other_fallback() -> None:
    prompt = build_system_prompt(_FIXTURE_TYPES)
    assert "dividends" in prompt
    assert "Дивиденды" in prompt
    assert "синонимы: Объявление дивидендов" in prompt
    assert "other" in prompt
    assert "Резервный код" in prompt
    assert "не выдумывай" not in prompt.lower() or "Не выдумывай" in prompt


def test_system_prompt_is_deterministic() -> None:
    a = build_system_prompt(_FIXTURE_TYPES)
    b = build_system_prompt(_FIXTURE_TYPES)
    assert a == b
