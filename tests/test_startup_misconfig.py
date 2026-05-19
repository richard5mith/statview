from __future__ import annotations

import pytest

from app.config import Settings
from app.main import create_app
from tests.test_support import TEST_DB_PATH


def _settings() -> Settings:
    return Settings(
        prometheus_url="http://example",
        live_refresh_seconds=5,
        saved_db_path=str(TEST_DB_PATH),
    )


@pytest.mark.usefixtures("test_db")
def test_missing_secret_key_serves_config_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("AUTH_TYPE", raising=False)
    app = create_app(settings=_settings(), run_migrations=False)

    response = app.test_client().get("/")
    assert response.status_code == 500
    assert b"SECRET_KEY is required" in response.data


@pytest.mark.usefixtures("test_db")
def test_healthz_also_returns_500_when_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    app = create_app(settings=_settings(), run_migrations=False)

    response = app.test_client().get("/healthz")
    assert response.status_code == 500
    assert b"SECRET_KEY is required" in response.data


@pytest.mark.usefixtures("test_db")
def test_unknown_auth_type_serves_config_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "saml")
    app = create_app(settings=_settings(), run_migrations=False)

    response = app.test_client().get("/anything")
    assert response.status_code == 500
    assert b"unknown AUTH_TYPE: saml" in response.data


@pytest.mark.usefixtures("test_db")
def test_github_mode_with_missing_client_id_serves_config_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    app = create_app(settings=_settings(), run_migrations=False)

    response = app.test_client().get("/")
    assert response.status_code == 500
    assert b"GITHUB_CLIENT_ID" in response.data
