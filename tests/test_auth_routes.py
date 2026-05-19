from __future__ import annotations

from typing import Any

import pytest
from flask import Flask, redirect

from app.auth.github import GitHubAuthError
from app.config import Settings
from app.main import create_app
from tests.test_support import TEST_DB_PATH


class FakeGitHubClient:
    def __init__(self) -> None:
        self.login_to_return: str = "alice"
        self.token_to_return: dict[str, Any] = {"access_token": "tok"}
        self.complete_raises: Exception | None = None
        self.org_membership: dict[tuple[str, str], bool] = {}
        self.org_raises: Exception | None = None
        self.start_called_with: str | None = None

    def start_oauth(self, redirect_uri: str):
        self.start_called_with = redirect_uri
        return redirect(f"https://github.example/oauth?redirect={redirect_uri}", code=302)

    def complete_oauth(self) -> tuple[str, dict[str, Any]]:
        if self.complete_raises:
            raise self.complete_raises
        return self.login_to_return, self.token_to_return

    def is_org_member(self, login: str, org: str, token: dict[str, Any]) -> bool:
        if self.org_raises:
            raise self.org_raises
        return self.org_membership.get((login.lower(), org.lower()), False)


def _auth_app(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeGitHubClient,
    *,
    allowed_users: str = "alice",
    allowed_org: str | None = None,
) -> Flask:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", allowed_users)
    if allowed_org:
        monkeypatch.setenv("GITHUB_ALLOWED_ORG", allowed_org)
    else:
        monkeypatch.delenv("GITHUB_ALLOWED_ORG", raising=False)

    app = create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        run_migrations=False,
        github_client=fake,
    )
    return app


@pytest.mark.usefixtures("test_db")
def test_login_page_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/login")
    assert response.status_code == 200
    assert b"Sign in with GitHub" in response.data


@pytest.mark.usefixtures("test_db")
def test_login_page_shows_revoked_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/login?reason=revoked")
    assert response.status_code == 200
    assert b"Your access was revoked" in response.data


@pytest.mark.usefixtures("test_db")
def test_start_redirects_to_github(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/auth/start")
    assert response.status_code == 302
    assert "github.example" in response.headers["Location"]
    assert fake.start_called_with is not None
    assert fake.start_called_with.endswith("/auth/callback")


@pytest.mark.usefixtures("test_db")
def test_callback_success_sets_session_and_redirects_to_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.login_to_return = "alice"
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    response = client.get("/auth/callback")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    with client.session_transaction() as sess:
        assert sess["github_login"] == "alice"
        assert sess["github_token"] == {"access_token": "tok"}


@pytest.mark.usefixtures("test_db")
def test_callback_redirects_to_next_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["next"] = "/dashboards"
    response = client.get("/auth/callback")
    assert response.headers["Location"] == "/dashboards"


@pytest.mark.usefixtures("test_db")
def test_callback_allowlist_denial_renders_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.login_to_return = "intruder"
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    response = client.get("/auth/callback")
    assert response.status_code == 403
    assert b"intruder" in response.data
    assert b"isn&#39;t authorized" in response.data or b"isn't authorized" in response.data
    with client.session_transaction() as sess:
        assert "github_login" not in sess


@pytest.mark.usefixtures("test_db")
def test_callback_oauth_error_renders_forbidden_with_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.complete_raises = GitHubAuthError("Bad credentials")
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/auth/callback")
    assert response.status_code == 403
    assert b"Sign-in failed" in response.data
    assert b"Bad credentials" in response.data


@pytest.mark.usefixtures("test_db")
def test_callback_truncates_long_error_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.complete_raises = GitHubAuthError("x" * 500)
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/auth/callback")
    assert response.status_code == 403
    body = response.data.decode("utf-8")
    assert "x" * 200 in body
    assert "x" * 250 not in body


@pytest.mark.usefixtures("test_db")
def test_callback_org_allowlist_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    fake.login_to_return = "bob"
    fake.org_membership[("bob", "acme")] = True
    app = _auth_app(monkeypatch, fake, allowed_users="", allowed_org="acme")
    response = app.test_client().get("/auth/callback")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


@pytest.mark.usefixtures("test_db")
def test_callback_org_allowlist_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    fake.login_to_return = "bob"
    fake.org_membership[("bob", "acme")] = False
    app = _auth_app(monkeypatch, fake, allowed_users="", allowed_org="acme")
    response = app.test_client().get("/auth/callback")
    assert response.status_code == 403
    assert b"isn&#39;t authorized" in response.data or b"isn't authorized" in response.data


@pytest.mark.usefixtures("test_db")
def test_logout_clears_session_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "alice"
        sess["github_token"] = {"access_token": "tok"}
    response = client.post("/logout")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert "github_login" not in sess


@pytest.mark.usefixtures("test_db")
def test_logout_does_not_accept_get(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/logout")
    assert response.status_code == 405
