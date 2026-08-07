"""Thin wrapper around the Garmin client adding throttling + bounded retries.

Every Garmin call goes through `Client.call()`, which:
  * sleeps THROTTLE_SECONDS before each request (polite, human-paced),
  * retries transient errors with exponential backoff (tenacity),
  * treats an auth failure as fatal (surfaced to the caller — never a silent
    credential re-login loop).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config
from garmin_coach import auth

log = logging.getLogger(__name__)


class TransientGarminError(RuntimeError):
    """A retryable error (timeout / 429 / 5xx)."""


# Garmin's library raises garminconnect.GarminConnect*Error subclasses. We keep
# the import soft so this module imports even if the exact names shift.
try:  # pragma: no cover - defensive import
    from garminconnect import (
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    _RETRYABLE = (
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
        TransientGarminError,
    )
except Exception:  # noqa: BLE001
    _RETRYABLE = (TransientGarminError,)


class Client:
    def __init__(self, garmin=None):
        self.garmin = garmin or auth.login()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def call(self, method: str, *args, **kwargs) -> Any:
        """Invoke a named Garmin method with throttle + retry.

        Returns None on a "no data for that day" style empty response rather
        than raising, so callers can log `missing` cleanly.
        """
        time.sleep(config.THROTTLE_SECONDS)
        fn: Callable = getattr(self.garmin, method)
        try:
            return fn(*args, **kwargs)
        except _RETRYABLE:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(t in msg for t in ("429", "too many", "timeout", "temporarily")):
                raise TransientGarminError(str(exc)) from exc
            # Non-retryable (incl. auth). Let it propagate.
            raise
