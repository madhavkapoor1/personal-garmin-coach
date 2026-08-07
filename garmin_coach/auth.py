"""Garmin authentication built around a persistent token cache.

Design goals (see plan, Phase 0):
  * Unattended runs must NOT need a password or MFA. They load cached OAuth
    tokens and let the library auto-refresh the short-lived access token.
  * MFA can only be satisfied interactively, so a full credential login lives
    in scripts/bootstrap_login.py and is a rare, manual event.
  * Never headless-retry a credential login on failure — that's the issue-#312
    trap. `login()` here only ever loads the cache; if the cache is gone or the
    refresh token is dead, it raises TokenExpiredError for the caller to surface.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from garminconnect import Garmin

import config

log = logging.getLogger(__name__)


class TokenExpiredError(RuntimeError):
    """Raised when no usable token cache exists and re-bootstrap is required."""


def login() -> Garmin:
    """Return an authenticated Garmin client using ONLY the cached tokens.

    Intended for the nightly job, the dashboard, and the MCP server. Raises
    TokenExpiredError if the cache is missing/expired so the caller can alert
    the user to re-run bootstrap_login.py rather than silently looping.
    """
    tokenstore = str(config.TOKENSTORE)
    client = Garmin()
    try:
        # garth.resume-style load: no email/password, no network round-trip
        # beyond a token refresh. Works as long as the refresh token is valid.
        client.login(tokenstore)
    except Exception as exc:  # noqa: BLE001 - normalize to our sentinel
        raise TokenExpiredError(
            f"Could not load Garmin tokens from {tokenstore}. "
            "Re-run: python scripts/bootstrap_login.py"
        ) from exc
    return client


def bootstrap_login(
    email: str,
    password: str,
    prompt_mfa: Optional[Callable[[], str]] = None,
) -> Garmin:
    """Full interactive login. Writes the token cache for future unattended use.

    Called only by scripts/bootstrap_login.py. `prompt_mfa` is invoked by the
    library only if 2FA is enabled on the account.

    In garminconnect >=0.3.x, passing the tokenstore path to login() makes it
    persist the OAuth tokens automatically (no separate dump() call).
    """
    tokenstore = str(config.TOKENSTORE)
    config.TOKENSTORE.mkdir(parents=True, exist_ok=True)
    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    client.login(tokenstore)  # logs in AND writes the token cache to tokenstore
    log.info("Token cache written to %s", tokenstore)
    return client


def check() -> bool:
    """Quick health check: can we load tokens and hit one lightweight endpoint?"""
    try:
        client = login()
        name = client.get_full_name()
        log.info("Authenticated as %s", name)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Auth check failed: %s", exc)
        return False
