from __future__ import annotations

from typing import Any

from app.auth.config import AuthConfig
from app.auth.github import GitHubClient


def is_allowed(
    login: str,
    token: dict[str, Any],
    cfg: AuthConfig,
    client: GitHubClient,
) -> bool:
    """Return True iff `login` is permitted under `cfg`.

    Raises GitHubUnreachableError if org membership cannot be verified.
    Callers map that to their own transport-layer behaviour (the callback
    fails closed; the gate renders a 503).
    """
    if login.lower() in cfg.allowed_users:
        return True
    if cfg.allowed_org is None:
        return False
    return client.is_org_member(login, cfg.allowed_org, token)
