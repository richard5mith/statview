from __future__ import annotations

import pytest

from app.auth.config import AuthConfig, ConfigError, load_auth_config


def test_secret_key_required_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("AUTH_TYPE", raising=False)
    with pytest.raises(ConfigError, match="SECRET_KEY is required"):
        load_auth_config()


def test_auth_type_none_returns_disabled_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "none")
    cfg = load_auth_config()
    assert isinstance(cfg, AuthConfig)
    assert cfg.enabled is False
    assert cfg.secret_key == "x" * 32


def test_auth_type_unset_returns_disabled_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.delenv("AUTH_TYPE", raising=False)
    cfg = load_auth_config()
    assert cfg.enabled is False


def test_auth_type_empty_string_returns_disabled_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "")
    cfg = load_auth_config()
    assert cfg.enabled is False


def test_auth_type_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "GitHub")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    cfg = load_auth_config()
    assert cfg.enabled is True


def test_unknown_auth_type_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "saml")
    with pytest.raises(ConfigError, match="unknown AUTH_TYPE: saml"):
        load_auth_config()


def test_github_mode_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    with pytest.raises(ConfigError, match="GITHUB_CLIENT_ID"):
        load_auth_config()


def test_github_mode_requires_client_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    with pytest.raises(ConfigError, match="GITHUB_CLIENT_SECRET"):
        load_auth_config()


def test_github_mode_requires_at_least_one_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.delenv("GITHUB_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GITHUB_ALLOWED_ORG", raising=False)
    with pytest.raises(
        ConfigError,
        match="at least one of GITHUB_ALLOWED_USERS or GITHUB_ALLOWED_ORG",
    ):
        load_auth_config()


def test_github_mode_users_allowlist_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "Alice, bob ,charlie,")
    cfg = load_auth_config()
    assert cfg.allowed_users == frozenset({"alice", "bob", "charlie"})
    assert cfg.allowed_org is None


def test_github_mode_org_allowlist_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_ORG", "Acme-Co")
    monkeypatch.delenv("GITHUB_ALLOWED_USERS", raising=False)
    cfg = load_auth_config()
    assert cfg.allowed_org == "acme-co"
    assert cfg.allowed_users == frozenset()


def test_github_mode_both_allowlists_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.setenv("GITHUB_ALLOWED_ORG", "acme")
    cfg = load_auth_config()
    assert cfg.allowed_users == frozenset({"alice"})
    assert cfg.allowed_org == "acme"


def test_oauth_redirect_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.setenv("OAUTH_REDIRECT_URL", "https://example.com/auth/callback")
    cfg = load_auth_config()
    assert cfg.oauth_redirect_url == "https://example.com/auth/callback"


def test_oauth_redirect_url_default_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("AUTH_TYPE", "github")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_ALLOWED_USERS", "alice")
    monkeypatch.delenv("OAUTH_REDIRECT_URL", raising=False)
    cfg = load_auth_config()
    assert cfg.oauth_redirect_url is None
