"""Patch 22: ``config/*.yaml.template`` files must round-trip through the
real Pydantic models so the documentation can't drift away from the live
schema (anyone copying a template into ``config/`` should get a runnable
config out of the box)."""

from __future__ import annotations

from pathlib import Path

import yaml

from edx.config import MetricsConfig, TickersConfig

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_tickers_template_loads() -> None:
    template = CONFIG_DIR / "tickers.yaml.template"
    assert template.is_file(), "tickers.yaml.template missing"
    data = yaml.safe_load(template.read_text(encoding="utf-8"))
    cfg = TickersConfig.model_validate(data)
    assert cfg.tickers, "template should ship with at least one example"
    profiles = {t.profile for t in cfg.tickers}
    # Template should illustrate both profiles (Patch 19) so a fresh
    # operator sees what `bank` looks like next to `non_bank`.
    assert {"bank", "non_bank"}.issubset(profiles), (
        f"template should include both profiles; got {profiles}"
    )


def test_metrics_template_loads() -> None:
    template = CONFIG_DIR / "metrics.yaml.template"
    assert template.is_file(), "metrics.yaml.template missing"
    data = yaml.safe_load(template.read_text(encoding="utf-8"))
    cfg = MetricsConfig.model_validate(data)
    # Both profiles required by Patch 19.
    assert "non_bank" in cfg.profiles
    assert "bank" in cfg.profiles
    # Each profile has at least one metric and a non-empty priority list.
    for name, profile in cfg.profiles.items():
        assert profile.metrics, f"profile {name!r} has no metrics"
        assert profile.reporting_priority, (
            f"profile {name!r} has empty reporting_priority"
        )
