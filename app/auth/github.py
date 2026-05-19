from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from authlib.integrations.flask_client import OAuth
from flask import Flask, Response


class GitHubError(Exception):
    """Base exception for the GitHub client."""


class GitHubAuthError(GitHubError):
    """OAuth flow failure: state mismatch, token exchange failed, /user failed."""


class GitHubUnreachableError(GitHubError):
    """Transient failure when calling GitHub (network, 5xx, rate limit)."""


class OrgMemberCache:
    """In-memory TTL cache for org-membership answers."""

    def __init__(
        self,
        ttl_seconds: int = 3600,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._time = time_fn
        self._entries: dict[tuple[str, str], tuple[bool, float]] = {}

    def get(self, login: str, org: str) -> bool | None:
        entry = self._entries.get((login.lower(), org.lower()))
        if entry is None:
            return None
        value, expires_at = entry
        if self._time() >= expires_at:
            del self._entries[(login.lower(), org.lower())]
            return None
        return value

    def put(self, login: str, org: str, value: bool) -> None:
        self._entries[(login.lower(), org.lower())] = (
            value,
            self._time() + self._ttl,
        )


class GitHubClient(Protocol):
    """Interface used by routes and the gate.

    Production: AuthlibGitHubClient. Tests: an in-test fake injected via
    app.config["github_client"], same pattern as prometheus_client.
    """

    def start_oauth(self, redirect_uri: str) -> Response: ...

    def complete_oauth(self) -> tuple[str, dict[str, Any]]:
        """Exchange the OAuth code for a token and fetch the user login.

        Returns (login, token_dict). The token dict is stored in the session
        so it can authenticate later org-membership calls.
        Raises GitHubAuthError on any failure with a user-displayable message.
        """
        ...

    def is_org_member(self, login: str, org: str, token: dict[str, Any]) -> bool:
        """True iff `login` is currently a member of `org`.

        Uses the OrgMemberCache. Raises GitHubUnreachableError on transient
        failures (network, 5xx, rate limit) so the gate can render
        auth_unavailable.html.
        """
        ...


class AuthlibGitHubClient:
    """Production implementation: authlib for OAuth + httpx via authlib for API."""

    def __init__(
        self,
        oauth: OAuth,
        cache: OrgMemberCache | None = None,
    ) -> None:
        self._oauth = oauth
        self._cache = cache or OrgMemberCache()

    def start_oauth(self, redirect_uri: str) -> Response:
        return self._oauth.github.authorize_redirect(redirect_uri)

    def complete_oauth(self) -> tuple[str, dict[str, Any]]:
        from authlib.integrations.base_client.errors import OAuthError

        try:
            token = self._oauth.github.authorize_access_token()
        except OAuthError as exc:
            raise GitHubAuthError(f"OAuth token exchange failed: {exc}") from exc

        try:
            response = self._oauth.github.get("user", token=token)
            response.raise_for_status()
        except Exception as exc:
            raise GitHubAuthError(f"GitHub /user request failed: {exc}") from exc

        data = response.json()
        login = data.get("login")
        if not isinstance(login, str) or not login:
            raise GitHubAuthError("GitHub /user response missing 'login'")
        return login, dict(token)

    def is_org_member(self, login: str, org: str, token: dict[str, Any]) -> bool:
        cached = self._cache.get(login, org)
        if cached is not None:
            return cached

        try:
            response = self._oauth.github.get(
                f"user/memberships/orgs/{org}",
                token=token,
            )
        except Exception as exc:
            raise GitHubUnreachableError(f"network error: {exc}") from exc

        if response.status_code == 200:
            is_member = response.json().get("state") == "active"
            self._cache.put(login, org, is_member)
            return is_member
        if response.status_code == 404:
            self._cache.put(login, org, False)
            return False
        if response.status_code in (403, 429):
            reset = response.headers.get("X-RateLimit-Reset", "unknown")
            raise GitHubUnreachableError(f"GitHub rate limit reached; resets at epoch {reset}")
        if 500 <= response.status_code < 600:
            raise GitHubUnreachableError(
                f"GitHub returned {response.status_code} on org membership check"
            )
        raise GitHubUnreachableError(
            f"unexpected GitHub status {response.status_code}: {response.text[:200]}"
        )


def build_oauth(app: Flask, client_id: str, client_secret: str) -> OAuth:
    """Register the GitHub provider with authlib."""
    oauth = OAuth(app)
    oauth.register(
        name="github",
        client_id=client_id,
        client_secret=client_secret,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user read:org"},
    )
    return oauth
