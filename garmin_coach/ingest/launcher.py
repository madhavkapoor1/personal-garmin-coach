"""Run an ingest from inside the dashboard, as a subprocess.

Deliberately shells out to the same `python -m garmin_coach.ingest.run` CLI the
nightly scheduled task uses, rather than importing and calling it in-process:

* the dashboard holds **read-only** connections; ingestion is the one writer,
  and keeping it in its own process keeps that boundary intact.
* the Garmin client is unofficial and can hang or die on auth. A subprocess can
  be timed out and killed; an in-process call would take Streamlit down with it.
* one code path for the button, the terminal, and Task Scheduler — so a fix in
  `run.py` reaches all three, and the button can't drift into its own dialect.

Exit codes come from `run.py`: 0 ok, 3 tokens expired (needs an interactive
`bootstrap_login.py`), anything else a genuine failure.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

TOKENS_EXPIRED = 3
DEFAULT_TIMEOUT_S = 900

# A pull holds the Garmin session and is rate-limited; two at once would double
# the request rate against an unofficial API. One machine-wide lock.
_LOCK = "ingest.lock"
_LOCK_STALE_S = 1800


@dataclass
class IngestResult:
    ok: bool = False
    returncode: Optional[int] = None
    tokens_expired: bool = False
    error: Optional[str] = None
    elapsed_s: float = 0.0
    lines: list[str] = field(default_factory=list)
    activities: int = 0
    days_done: int = 0
    datasets_failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One line a human can read at a glance."""
        if self.tokens_expired:
            return "Garmin tokens expired — re-run scripts/bootstrap_login.py."
        if not self.ok:
            return self.error or f"Ingest failed (exit {self.returncode})."
        bits = []
        if self.days_done:
            bits.append(f"{self.days_done} day{'s' if self.days_done != 1 else ''} of wellness")
        if self.activities:
            bits.append(f"{self.activities} activit{'ies' if self.activities != 1 else 'y'}")
        got = ", ".join(bits) if bits else "no new data"
        note = (f" · {len(self.datasets_failed)} dataset error(s)"
                if self.datasets_failed else "")
        return f"Pulled {got} in {self.elapsed_s:.0f}s{note}."


def _lock_path() -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / _LOCK


def is_running() -> bool:
    """True if another ingest holds the lock and it has not gone stale."""
    p = _lock_path()
    if not p.exists():
        return False
    if time.time() - p.stat().st_mtime > _LOCK_STALE_S:
        # A killed process leaves the file behind; don't lock the user out.
        p.unlink(missing_ok=True)
        return False
    return True


def _build_args(mode: str, days: int, day: Optional[str], with_streams: bool) -> list[str]:
    args = [sys.executable, "-m", "garmin_coach.ingest.run"]
    if mode == "nightly":
        args.append("--nightly")
    elif mode == "date":
        args += ["--date", str(day)]
    elif mode == "backfill":
        args += ["--backfill", str(days)]
    else:
        raise ValueError(f"unknown ingest mode {mode!r}")
    if not with_streams:
        args.append("--no-streams")
    return args


_RE_ACTIVITIES = re.compile(r"ingested (\d+) activities")
_RE_DAY = re.compile(r"\[\d+/\d+\]\s+(?:wellness|\S+)")


def run_ingest(
    mode: str = "nightly",
    *,
    days: int = 7,
    day: Optional[str] = None,
    with_streams: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Iterator[tuple[str, object]]:
    """Run an ingest, yielding ``("log", line)`` then a final ``("done", result)``.

    A ``done`` is always yielded, including on failure, so callers render one
    terminal state without catching exceptions.
    """
    result = IngestResult()
    if is_running():
        result.error = ("Another pull is already running. Wait for it to finish, "
                        f"or delete {_lock_path()} if it crashed.")
        yield "done", result
        return

    lock = _lock_path()
    started = time.monotonic()
    proc = None
    try:
        lock.write_text(str(os.getpid()), encoding="utf-8")
        proc = subprocess.Popen(
            _build_args(mode, days, day, with_streams),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # ingest logs to stderr; one ordered stream
            stdin=subprocess.DEVNULL,  # never let a prompt block us invisibly
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(config.PROJECT_ROOT),
            env={**os.environ, "GARMIN_DB": str(config.DB_PATH), "PYTHONUTF8": "1"},
        )
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            result.lines.append(line)
            if m := _RE_ACTIVITIES.search(line):
                result.activities += int(m.group(1))
            if _RE_DAY.search(line):
                result.days_done += 1
            if "'error'" in line:
                result.datasets_failed.append(line)
            yield "log", line

            if time.monotonic() - started > timeout_s:
                proc.kill()
                result.error = f"Ingest timed out after {timeout_s}s."
                break
    except OSError as exc:
        result.error = f"Could not start ingest: {exc}"
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            result.returncode = proc.returncode
        lock.unlink(missing_ok=True)

    result.elapsed_s = time.monotonic() - started
    result.tokens_expired = result.returncode == TOKENS_EXPIRED
    result.ok = result.returncode == 0 and result.error is None
    if not result.ok and not result.error and not result.tokens_expired:
        tail = " / ".join(result.lines[-3:]) if result.lines else "no output"
        result.error = f"Ingest exited {result.returncode}: {tail}"
    yield "done", result


def last_ingest_at() -> Optional[str]:
    """Most recent successful ingest timestamp, straight from `ingest_log`."""
    from garmin_coach import db  # local import: keeps CLI startup cheap

    try:
        conn = db.connect_ro()
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM ingest_log WHERE status = 'ok'"
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        conn.close()
