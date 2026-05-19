from __future__ import annotations

from typing import Any

import pytest

from app.auth.github import (
    AuthlibGitHubClient,
    GitHubUnreachableError,
    OrgMemberCache,
)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._json


class FakeGithubProvider:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, token: dict[str, Any]) -> FakeResponse:
        self.calls.append((path, token))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeOAuth:
    def __init__(self, github: FakeGithubProvider) -> None:
        self.github = github


def test_cache_returns_none_for_missing_entry() -> None:
    cache = OrgMemberCache(ttl_seconds=60, time_fn=FakeClock())
    assert cache.get("alice", "acme") is None


def test_cache_returns_stored_value_within_ttl() -> None:
    clock = FakeClock()
    cache = OrgMemberCache(ttl_seconds=60, time_fn=clock)
    cache.put("alice", "acme", True)
    clock.now += 30
    assert cache.get("alice", "acme") is True


def test_cache_returns_none_after_ttl_expiry() -> None:
    clock = FakeClock()
    cache = OrgMemberCache(ttl_seconds=60, time_fn=clock)
    cache.put("alice", "acme", True)
    clock.now += 61
    assert cache.get("alice", "acme") is None


def test_cache_caches_negative_results() -> None:
    cache = OrgMemberCache(ttl_seconds=60, time_fn=FakeClock())
    cache.put("alice", "acme", False)
    assert cache.get("alice", "acme") is False


def test_cache_is_keyed_per_login_per_org() -> None:
    cache = OrgMemberCache(ttl_seconds=60, time_fn=FakeClock())
    cache.put("alice", "acme", True)
    cache.put("alice", "other", False)
    cache.put("bob", "acme", False)
    assert cache.get("alice", "acme") is True
    assert cache.get("alice", "other") is False
    assert cache.get("bob", "acme") is False


def _build_client(response: FakeResponse | Exception) -> AuthlibGitHubClient:
    return AuthlibGitHubClient(FakeOAuth(FakeGithubProvider(response)))  # type: ignore[arg-type]


def test_is_org_member_returns_true_for_active_membership() -> None:
    client = _build_client(FakeResponse(200, json_body={"state": "active"}))
    assert client.is_org_member("alice", "acme", {"access_token": "t"}) is True


def test_is_org_member_returns_false_for_pending_membership() -> None:
    client = _build_client(FakeResponse(200, json_body={"state": "pending"}))
    assert client.is_org_member("alice", "acme", {"access_token": "t"}) is False


def test_is_org_member_returns_false_for_404() -> None:
    client = _build_client(FakeResponse(404))
    assert client.is_org_member("alice", "acme", {"access_token": "t"}) is False


def test_is_org_member_raises_on_403_rate_limit_with_reset_header() -> None:
    client = _build_client(FakeResponse(403, headers={"X-RateLimit-Reset": "1234567890"}))
    with pytest.raises(GitHubUnreachableError, match="resets at epoch 1234567890"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_raises_on_429_rate_limit() -> None:
    client = _build_client(FakeResponse(429, headers={"X-RateLimit-Reset": "9999"}))
    with pytest.raises(GitHubUnreachableError, match="rate limit"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_rate_limit_without_reset_header_falls_back_to_unknown() -> None:
    client = _build_client(FakeResponse(403))
    with pytest.raises(GitHubUnreachableError, match="resets at epoch unknown"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_raises_on_5xx() -> None:
    client = _build_client(FakeResponse(503))
    with pytest.raises(GitHubUnreachableError, match="GitHub returned 503"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_raises_on_unexpected_status() -> None:
    client = _build_client(FakeResponse(418, text="i am a teapot"))
    with pytest.raises(GitHubUnreachableError, match="unexpected GitHub status 418"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_raises_on_network_exception() -> None:
    client = _build_client(RuntimeError("connection refused"))
    with pytest.raises(GitHubUnreachableError, match="network error"):
        client.is_org_member("alice", "acme", {"access_token": "t"})


def test_is_org_member_uses_cache_on_repeat_call() -> None:
    provider = FakeGithubProvider(FakeResponse(200, json_body={"state": "active"}))
    client = AuthlibGitHubClient(FakeOAuth(provider))  # type: ignore[arg-type]
    assert client.is_org_member("alice", "acme", {"access_token": "t"}) is True
    assert client.is_org_member("alice", "acme", {"access_token": "t"}) is True
    assert len(provider.calls) == 1
