"""Shared fixtures for storage-layer tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from edx.storage import Database


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    yield db


@pytest.fixture
def conn(tmp_db: Database) -> Iterator[sqlite3.Connection]:
    connection = tmp_db.connect()
    try:
        yield connection
    finally:
        connection.close()
