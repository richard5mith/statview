from __future__ import annotations

import os
from dataclasses import dataclass

SESSION_LIFETIME_DAYS = 30
ORG_CACHE_TTL_SECONDS = 3600


class ConfigError(Exception):
    """Raised when auth-related env vars are invalid or incomplete."""


@dataclass(frozen=True)
class AuthConfig:
    secret_key: str
    enabled: bool
    client_id: str = ""
    client_secret: str = ""
    allowed_users: frozenset[str] = frozenset()
    allowed_org: str | None = None
    oauth_redirect_url: str | None = None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def load_auth_config() -> AuthConfig:
    secret_key = _env("SECRET_KEY")
    if not secret_key:
        raise ConfigError("SECRET_KEY is required")

    auth_type = _env("AUTH_TYPE").lower()
    if auth_type in ("", "none"):
        return AuthConfig(secret_key=secret_key, enabled=False)
    if auth_type != "github":
        raise ConfigError(f"unknown AUTH_TYPE: {auth_type}; expected one of: none, github")

    missing: list[str] = []
    client_id = _env("GITHUB_CLIENT_ID")
    if not client_id:
        missing.append("GITHUB_CLIENT_ID")
    client_secret = _env("GITHUB_CLIENT_SECRET")
    if not client_secret:
        missing.append("GITHUB_CLIENT_SECRET")
    if missing:
        raise ConfigError(f"AUTH_TYPE=github requires: {', '.join(missing)}")

    raw_users = _env("GITHUB_ALLOWED_USERS")
    allowed_users = frozenset(part.strip().lower() for part in raw_users.split(",") if part.strip())
    allowed_org_raw = _env("GITHUB_ALLOWED_ORG")
    allowed_org = allowed_org_raw.lower() if allowed_org_raw else None

    if not allowed_users and not allowed_org:
        raise ConfigError(
            "AUTH_TYPE=github requires at least one of GITHUB_ALLOWED_USERS or GITHUB_ALLOWED_ORG"
        )

    redirect_url = _env("OAUTH_REDIRECT_URL") or None

    return AuthConfig(
        secret_key=secret_key,
        enabled=True,
        client_id=client_id,
        client_secret=client_secret,
        allowed_users=allowed_users,
        allowed_org=allowed_org,
        oauth_redirect_url=redirect_url,
    )
