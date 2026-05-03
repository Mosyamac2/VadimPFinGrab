"""Synth: writing config-evolve/ for one tick."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from edx.evolve.csv_loader import CompanyRow
from edx.evolve.synth import SYMLINKED_FILES, write_evolve_config

BATCH = [
    CompanyRow(company_id="1210", name="Банк ВТБ (ПАО)", type="bank"),
    CompanyRow(company_id="38588", name="ПАО иэк холдинг", type="non_bank"),
    CompanyRow(company_id="2541", name='АО "Карельский окатыш"', type="non_bank"),
]


def _make_base_config(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "app.yaml").write_text(
        "paths:\n  data_dir: data\n"
        "mode:\n  backfill_years: 3\n  default_run_mode: update\n"
        "discoverer:\n  base_url: https://example.com\n",
        encoding="utf-8",
    )
    for name in SYMLINKED_FILES:
        (base / name).write_text(f"# {name}\n", encoding="utf-8")


def test_synth_writes_tickers_yaml(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)

    write_evolve_config(BATCH, target_dir=target, base_dir=base)

    payload = yaml.safe_load((target / "tickers.yaml").read_text(encoding="utf-8"))
    assert "tickers" in payload
    assert len(payload["tickers"]) == 3
    first = payload["tickers"][0]
    assert first["ticker"] == "EDX1210"
    assert first["e_disclosure_id"] == "1210"
    assert first["profile"] == "bank"


def test_synth_app_yaml_overrides_backfill_years(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)

    write_evolve_config(BATCH, target_dir=target, base_dir=base)
    data = yaml.safe_load((target / "app.yaml").read_text(encoding="utf-8"))
    assert data["mode"]["backfill_years"] == 1
    # Other fields preserved.
    assert data["mode"]["default_run_mode"] == "update"
    assert data["paths"]["data_dir"] == "data"


def test_synth_creates_symlinks(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)

    write_evolve_config(BATCH, target_dir=target, base_dir=base)
    for name in SYMLINKED_FILES:
        link = target / name
        assert link.is_symlink(), f"{name} should be a symlink"
        # Symlink must resolve to the base file.
        assert link.resolve() == (base / name).resolve()


def test_synth_idempotent(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)

    write_evolve_config(BATCH, target_dir=target, base_dir=base)
    snapshot1 = (target / "tickers.yaml").read_text(encoding="utf-8")
    write_evolve_config(BATCH, target_dir=target, base_dir=base)
    snapshot2 = (target / "tickers.yaml").read_text(encoding="utf-8")
    assert snapshot1 == snapshot2

    # All symlinks still in place.
    for name in SYMLINKED_FILES:
        assert (target / name).is_symlink()


def test_synth_replaces_stale_regular_file_with_symlink(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)
    target.mkdir(parents=True)
    # Pre-existing regular file at the target — must be replaced.
    (target / "metrics.yaml").write_text("STALE\n", encoding="utf-8")

    write_evolve_config(BATCH, target_dir=target, base_dir=base)

    metrics = target / "metrics.yaml"
    assert metrics.is_symlink()
    assert metrics.resolve() == (base / "metrics.yaml").resolve()


def test_synth_app_yaml_invalid_top_level_raises(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    base.mkdir()
    (base / "app.yaml").write_text("- list_top_level\n", encoding="utf-8")
    for name in SYMLINKED_FILES:
        (base / name).write_text("# .\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected mapping"):
        write_evolve_config(BATCH, target_dir=target, base_dir=base)


def test_synth_renders_unicode_intact(tmp_path: Path) -> None:
    base = tmp_path / "config"
    target = tmp_path / "config-evolve"
    _make_base_config(base)
    write_evolve_config(BATCH, target_dir=target, base_dir=base)
    raw = (target / "tickers.yaml").read_text(encoding="utf-8")
    assert "Банк ВТБ" in raw
    assert "иэк холдинг" in raw
    # Не должно быть escape-хвостов вида \u04
    assert "\\u04" not in raw
