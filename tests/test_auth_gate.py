from __future__ import annotations

from typing import Any

import pytest
from flask import Flask, redirect

from app.auth.github import GitHubUnreachableError
from app.config import Settings
from app.main import create_app
from tests.test_support import TEST_DB_PATH


class FakeGitHubClient:
    def __init__(self) -> None:
        self.org_membership: dict[tuple[str, str], bool] = {}
        self.org_raises: Exception | None = None
        self.org_calls: list[tuple[str, str]] = []

    def start_oauth(self, redirect_uri: str):
        return redirect("https://github.example/oauth", code=302)

    def complete_oauth(self) -> tuple[str, dict[str, Any]]:
        return "alice", {"access_token": "tok"}

    def is_org_member(self, login: str, org: str, token: dict[str, Any]) -> bool:
        self.org_calls.append((login.lower(), org.lower()))
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
    return create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        run_migrations=False,
        github_client=fake,
    )


@pytest.mark.usefixtures("test_db")
def test_unauthenticated_request_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    response = client.get("/dashboards")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert sess.get("next") == "/dashboards"


@pytest.mark.usefixtures("test_db")
def test_healthz_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/healthz")
    assert response.status_code == 200


@pytest.mark.usefixtures("test_db")
def test_login_route_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/login")
    assert response.status_code == 200


@pytest.mark.usefixtures("test_db")
def test_callback_route_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    response = app.test_client().get("/auth/callback")
    assert response.status_code in (200, 302, 403)
    assert "/login" not in (response.headers.get("Location") or "")


@pytest.mark.usefixtures("test_db")
def test_authenticated_user_in_allowlist_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "alice"
        sess["github_token"] = {"access_token": "tok"}
    response = client.get("/")
    assert response.status_code in (200, 503)
    assert response.status_code != 302


@pytest.mark.usefixtures("test_db")
def test_session_with_revoked_user_is_booted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake, allowed_users="alice")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "intruder"
        sess["github_token"] = {"access_token": "tok"}
    response = client.get("/dashboards")
    assert response.status_code == 302
    assert "reason=revoked" in response.headers["Location"]
    with client.session_transaction() as sess:
        assert "github_login" not in sess


@pytest.mark.usefixtures("test_db")
def test_org_allowlist_pass_uses_github_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.org_membership[("bob", "acme")] = True
    app = _auth_app(monkeypatch, fake, allowed_users="", allowed_org="acme")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "bob"
        sess["github_token"] = {"access_token": "tok"}
    response = client.get("/")
    assert response.status_code != 302
    assert ("bob", "acme") in fake.org_calls


@pytest.mark.usefixtures("test_db")
def test_org_allowlist_denial_boots_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.org_membership[("bob", "acme")] = False
    app = _auth_app(monkeypatch, fake, allowed_users="", allowed_org="acme")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "bob"
        sess["github_token"] = {"access_token": "tok"}
    response = client.get("/")
    assert response.status_code == 302
    assert "reason=revoked" in response.headers["Location"]


@pytest.mark.usefixtures("test_db")
def test_org_unavailable_renders_503_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHubClient()
    fake.org_raises = GitHubUnreachableError("GitHub returned 503 on org membership check")
    app = _auth_app(monkeypatch, fake, allowed_users="", allowed_org="acme")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "bob"
        sess["github_token"] = {"access_token": "tok"}
    response = client.get("/")
    assert response.status_code == 503
    body = response.data
    assert b"Can&#39;t verify access right now" in body or b"Can't verify access right now" in body
    assert b"GitHub returned 503" in body


@pytest.mark.usefixtures("test_db")
def test_user_allowlist_bypasses_org_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If login is in GITHUB_ALLOWED_USERS, no GitHub API call is made."""
    fake = FakeGitHubClient()
    app = _auth_app(monkeypatch, fake, allowed_users="alice", allowed_org="acme")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["github_login"] = "alice"
        sess["github_token"] = {"access_token": "tok"}
    client.get("/")
    assert fake.org_calls == []
