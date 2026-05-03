"""Shared fixtures for self-evolve tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from edx.storage import Database, EvolutionRepo


@pytest.fixture
def evolve_db(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    yield db


@pytest.fixture
def evolve_conn(evolve_db: Database) -> Iterator[sqlite3.Connection]:
    conn = evolve_db.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def evolve_repo(
    evolve_db: Database, evolve_conn: sqlite3.Connection
) -> EvolutionRepo:
    return EvolutionRepo(evolve_db, evolve_conn)


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Sample CSV with 6 companies (2 banks, 4 non_bank)."""
    p = tmp_path / "companies.csv"
    p.write_text(
        "id,name,type\n"
        "1210,Банк ВТБ (ПАО),bank\n"
        "1480,ПАО \"Аэрофлот\",non_bank\n"
        "2541,АО \"Карельский окатыш\",non_bank\n"
        "3043,ПАО Сбербанк,bank\n"
        "17,ПАО \"ЛУКОЙЛ\",non_bank\n"
        "38588,ПАО иэк холдинг,non_bank\n",
        encoding="utf-8",
    )
    return p
