from __future__ import annotations

import pytest

from app.config import Settings
from app.main import create_app
from tests.test_main import FakePrometheusClient
from tests.test_support import TEST_DB_PATH


def _app():
    return create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=FakePrometheusClient(),
        run_migrations=False,
    )


@pytest.mark.usefixtures("test_db")
def test_auth_type_unset_allows_unauthenticated_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.delenv("AUTH_TYPE", raising=False)
    response = _app().test_client().get("/")
    assert response.status_code == 200


@pytest.mark.usefixtures("test_db")
def test_auth_type_none_allows_unauthenticated_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "none")
    response = _app().test_client().get("/")
    assert response.status_code == 200


@pytest.mark.usefixtures("test_db")
def test_login_route_does_not_exist_when_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "none")
    response = _app().test_client().get("/login")
    assert response.status_code == 404


@pytest.mark.usefixtures("test_db")
def test_auth_callback_does_not_exist_when_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "none")
    response = _app().test_client().get("/auth/callback")
    assert response.status_code == 404
