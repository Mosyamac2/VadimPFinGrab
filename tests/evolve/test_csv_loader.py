"""csv_loader: validation and normalisation of e-disclosure-companies.csv."""

from __future__ import annotations

from pathlib import Path

import pytest

from edx.evolve.csv_loader import CompanyRow, load_companies


def test_load_companies_basic(csv_path: Path) -> None:
    rows = load_companies(csv_path)
    assert len(rows) == 6
    assert rows[0] == CompanyRow(
        company_id="1210", name="Банк ВТБ (ПАО)", type="bank"
    )
    assert rows[1].type == "non_bank"


def test_synthetic_ticker_format(csv_path: Path) -> None:
    rows = load_companies(csv_path)
    assert rows[0].synthetic_ticker == "EDX1210"
    assert rows[3].synthetic_ticker == "EDX3043"


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_companies(tmp_path / "absent.csv")


def test_load_missing_type_column(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("id,name\n1,foo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        load_companies(p)


def test_load_empty_id(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("id,name,type\n,foo,bank\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty id"):
        load_companies(p)


def test_load_empty_name(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("id,name,type\n1,,bank\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty name"):
        load_companies(p)


def test_load_invalid_type(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("id,name,type\n1,foo,unknown\n", encoding="utf-8")
    with pytest.raises(ValueError, match="type must be one of"):
        load_companies(p)


def test_load_normalises_uppercase_type(tmp_path: Path) -> None:
    p = tmp_path / "ok.csv"
    p.write_text("id,name,type\n1,foo,Bank\n", encoding="utf-8")
    rows = load_companies(p)
    assert rows[0].type == "bank"


def test_load_handles_quoted_commas(tmp_path: Path) -> None:
    p = tmp_path / "ok.csv"
    p.write_text(
        'id,name,type\n1,"АО ""СОЮЗ"", им. Жукова",non_bank\n',
        encoding="utf-8",
    )
    rows = load_companies(p)
    assert rows[0].name == 'АО "СОЮЗ", им. Жукова'
