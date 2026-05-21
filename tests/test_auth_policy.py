from __future__ import annotations

from typing import Any

import pytest

from app.auth.config import AuthConfig
from app.auth.github import GitHubUnreachableError
from app.auth.policy import is_allowed


class FakeGitHubClient:
    def __init__(self) -> None:
        self.org_membership: dict[tuple[str, str], bool] = {}
        self.org_raises: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def start_oauth(self, redirect_uri: str):  # not used by policy
        raise NotImplementedError

    def complete_oauth(self):  # not used by policy
        raise NotImplementedError

    def is_org_member(self, login: str, org: str, token: dict[str, Any]) -> bool:
        self.calls.append((login.lower(), org.lower()))
        if self.org_raises is not None:
            raise self.org_raises
        return self.org_membership.get((login.lower(), org.lower()), False)


def _cfg(*, allowed_users: tuple[str, ...] = (), allowed_org: str | None = None) -> AuthConfig:
    return AuthConfig(
        secret_key="x" * 32,
        enabled=True,
        client_id="id",
        client_secret="secret",
        allowed_users=frozenset(allowed_users),
        allowed_org=allowed_org,
    )


TOKEN: dict[str, Any] = {"access_token": "tok"}


def test_is_allowed_true_when_login_in_allowed_users() -> None:
    client = FakeGitHubClient()
    cfg = _cfg(allowed_users=("alice",))
    assert is_allowed("alice", TOKEN, cfg, client) is True


def test_is_allowed_case_insensitive_login_match() -> None:
    client = FakeGitHubClient()
    cfg = _cfg(allowed_users=("alice",))
    assert is_allowed("ALICE", TOKEN, cfg, client) is True


def test_is_allowed_in_allowed_users_skips_github_call() -> None:
    client = FakeGitHubClient()
    cfg = _cfg(allowed_users=("alice",), allowed_org="acme")
    is_allowed("alice", TOKEN, cfg, client)
    assert client.calls == []


def test_is_allowed_false_when_no_allowed_users_and_no_allowed_org() -> None:
    client = FakeGitHubClient()
    cfg = _cfg()
    assert is_allowed("alice", TOKEN, cfg, client) is False


def test_is_allowed_false_when_not_in_allowed_users_and_no_allowed_org() -> None:
    client = FakeGitHubClient()
    cfg = _cfg(allowed_users=("bob",))
    assert is_allowed("alice", TOKEN, cfg, client) is False


def test_is_allowed_true_when_confirmed_org_member() -> None:
    client = FakeGitHubClient()
    client.org_membership[("alice", "acme")] = True
    cfg = _cfg(allowed_org="acme")
    assert is_allowed("alice", TOKEN, cfg, client) is True
    assert client.calls == [("alice", "acme")]


def test_is_allowed_false_when_not_org_member() -> None:
    client = FakeGitHubClient()
    client.org_membership[("alice", "acme")] = False
    cfg = _cfg(allowed_org="acme")
    assert is_allowed("alice", TOKEN, cfg, client) is False


def test_is_allowed_propagates_unreachable_error() -> None:
    client = FakeGitHubClient()
    client.org_raises = GitHubUnreachableError("GitHub returned 503")
    cfg = _cfg(allowed_org="acme")
    with pytest.raises(GitHubUnreachableError):
        is_allowed("alice", TOKEN, cfg, client)


def test_is_allowed_propagates_unexpected_exception() -> None:
    """Policy is decision-only — unexpected exceptions are NOT swallowed."""
    client = FakeGitHubClient()
    client.org_raises = RuntimeError("authlib bug")
    cfg = _cfg(allowed_org="acme")
    with pytest.raises(RuntimeError):
        is_allowed("alice", TOKEN, cfg, client)
