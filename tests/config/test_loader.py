"""Loader behaviour: reference YAMLs, error mapping, no caching."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from edx.config import AppSettings, ConfigLoadError, load_all

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _copy_reference_configs(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        shutil.copy(src, target / src.name)


def test_load_reference_configs(tmp_path: Path) -> None:
    settings = load_all(REPO_CONFIG_DIR, env_file=tmp_path / "missing.env")
    assert isinstance(settings, AppSettings)
    assert settings.app.mode.backfill_years == 3
    assert settings.metrics.reporting_priority == ["IFRS", "RSBU"]
    assert {m.canonical_name for m in settings.metrics.metrics} == {
        "revenue",
        "ebitda",
        "net_income",
        "total_assets",
        "total_debt",
    }
    assert any(et.code == "other" for et in settings.event_types.event_types)
    assert settings.llm.primary.model == "claude-sonnet-4-6"


def test_load_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError) as excinfo:
        load_all(tmp_path / "does-not-exist")
    assert excinfo.value.file_path.name == "app.yaml"


def test_load_extra_field_reports_path_and_field(tmp_path: Path) -> None:
    _copy_reference_configs(tmp_path)
    metrics_path = tmp_path / "metrics.yaml"
    data = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))
    data["surprise_field"] = 42
    metrics_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ConfigLoadError) as excinfo:
        load_all(tmp_path)
    err = excinfo.value
    assert err.file_path == metrics_path
    assert err.field_path is not None
    assert "surprise_field" in err.field_path


def test_load_invalid_reporting_priority(tmp_path: Path) -> None:
    _copy_reference_configs(tmp_path)
    metrics_path = tmp_path / "metrics.yaml"
    data = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))
    data["reporting_priority"] = ["GAAP"]
    metrics_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ConfigLoadError) as excinfo:
        load_all(tmp_path)
    err = excinfo.value
    assert err.file_path == metrics_path
    assert err.field_path is not None and "reporting_priority" in err.field_path


def test_load_malformed_yaml(tmp_path: Path) -> None:
    _copy_reference_configs(tmp_path)
    (tmp_path / "app.yaml").write_text("paths: [not, a, mapping", encoding="utf-8")
    with pytest.raises(ConfigLoadError) as excinfo:
        load_all(tmp_path)
    assert excinfo.value.file_path.name == "app.yaml"


def test_load_top_level_not_mapping(tmp_path: Path) -> None:
    _copy_reference_configs(tmp_path)
    (tmp_path / "tickers.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError) as excinfo:
        load_all(tmp_path)
    assert excinfo.value.file_path.name == "tickers.yaml"


def test_load_is_not_cached(tmp_path: Path) -> None:
    _copy_reference_configs(tmp_path)
    s1 = load_all(tmp_path)
    # mutate after first load
    metrics_path = tmp_path / "metrics.yaml"
    data = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))
    data["reporting_priority"] = ["RSBU", "IFRS"]
    metrics_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    s2 = load_all(tmp_path)
    assert s1.metrics.reporting_priority == ["IFRS", "RSBU"]
    assert s2.metrics.reporting_priority == ["RSBU", "IFRS"]
