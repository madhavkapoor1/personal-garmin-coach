"""Central configuration. Reads .env when present, falls back to sane defaults.

Everything the rest of the app needs to know about *where* things live and the
athlete's HR markers is resolved here, so no other module hard-codes a path.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional; env vars still work without it
    pass

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.getenv("GARMIN_DB", DATA_DIR / "garmin.db")).expanduser()
SCHEMA_PATH = PROJECT_ROOT / "garmin_coach" / "schema.sql"

# Garmin token cache. python-garminconnect writes garmin_tokens.json here.
TOKENSTORE = Path(os.getenv("GARMIN_TOKENSTORE", "~/.garminconnect")).expanduser()

# --- Credentials -------------------------------------------------------------
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL", "")
# Password is intentionally NOT read from .env for unattended runs. It is only
# supplied interactively during bootstrap_login.py. If you insist on storing it,
# prefer keyring (see scripts/bootstrap_login.py --use-keyring).

# --- Athlete markers ---------------------------------------------------------
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


HR_ZONE2_CEILING = _int_env("HR_ZONE2_CEILING", 150)
HR_LTHR = _int_env("HR_LTHR", 170)
HR_MAX = _int_env("HR_MAX", 190)

TIMEZONE = os.getenv("TIMEZONE", "UTC")

# --- Ingestion tuning --------------------------------------------------------
# Seconds to sleep between Garmin API calls (be a polite, human-paced client).
THROTTLE_SECONDS = float(os.getenv("GARMIN_THROTTLE", "1.0"))
# Nightly job re-pulls this many trailing days to catch Garmin's late backfill.
NIGHTLY_TRAILING_DAYS = _int_env("NIGHTLY_TRAILING_DAYS", 3)


def ensure_dirs() -> None:
    """Create the data directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
