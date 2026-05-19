from __future__ import annotations

import os

import pytest

from app.db import migrate_database
from tests.test_support import DatabaseFixture


@pytest.fixture(scope="session", autouse=True)
def _default_secret_key() -> None:
    """SECRET_KEY is required by create_app. Default it for the test session
    unless an individual test overrides via monkeypatch."""
    os.environ.setdefault("SECRET_KEY", "test-secret-key-only-for-pytest")


@pytest.fixture(scope="session")
def test_db_config() -> DatabaseFixture:
    fixture = DatabaseFixture()
    fixture.db_path.parent.mkdir(parents=True, exist_ok=True)
    return fixture


@pytest.fixture
def test_db(test_db_config: DatabaseFixture) -> DatabaseFixture:
    test_db_config.reset()
    migrate_database(str(test_db_config.db_path))
    return test_db_config
