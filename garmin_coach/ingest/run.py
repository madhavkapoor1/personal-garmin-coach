"""Ingestion entry point.

    python -m garmin_coach.ingest.run --nightly
    python -m garmin_coach.ingest.run --date 2026-07-13
    python -m garmin_coach.ingest.run --backfill 365
    python -m garmin_coach.ingest.run --backfill 365 --no-streams

Exit codes: 0 ok, 3 auth/token failure (re-run bootstrap_login.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import config
from garmin_coach import db
from garmin_coach.auth import TokenExpiredError
from garmin_coach.ingest import activities, wellness
from garmin_coach.ingest.client import Client

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ingest")


def _days(end: dt.date, n: int):
    """Yield the n dates ending at `end` (most recent first)."""
    for i in range(n):
        yield (end - dt.timedelta(days=i)).isoformat()


def run(mode: str, n: int, date: str | None, with_streams: bool) -> int:
    db.init_db()
    try:
        client = Client()
    except TokenExpiredError as exc:
        log.error("AUTH FAILURE: %s", exc)
        return 3

    conn = db.connect()
    today = dt.date.today()

    if mode == "date":
        dates = [date]
        act_start, act_end = date, date
    elif mode == "backfill":
        dates = list(_days(today, n))
        act_start = (today - dt.timedelta(days=n - 1)).isoformat()
        act_end = today.isoformat()
    else:  # nightly
        dates = list(_days(today, config.NIGHTLY_TRAILING_DAYS))
        act_start = (today - dt.timedelta(days=config.NIGHTLY_TRAILING_DAYS)).isoformat()
        act_end = today.isoformat()

    # --- wellness (per day) ---
    for i, d in enumerate(dates, 1):
        # In backfill, skip days already fully OK (resumable).
        if mode == "backfill" and _all_done(conn, d):
            log.info("[%d/%d] %s already complete, skipping", i, len(dates), d)
            continue
        log.info("[%d/%d] wellness %s", i, len(dates), d)
        try:
            res = wellness.ingest_day(client, conn, d)
            log.info("   %s", res)
        except TokenExpiredError as exc:
            log.error("AUTH FAILURE mid-run: %s", exc)
            return 3

    # --- activities (range) ---
    log.info("activities %s..%s (streams=%s)", act_start, act_end, with_streams)
    count = activities.ingest_range(client, conn, act_start, act_end, with_streams)
    log.info("ingested %d activities", count)

    # --- personal records (whole-account, pulled once) ---
    _ingest_prs(client, conn)

    # --- training plan, workout templates, planned calendar (once) ---
    _ingest_plans(client, conn, act_start, act_end)

    conn.close()
    return 0


def _ingest_plans(client, conn, start, end) -> None:
    """Active training plan, workout templates, and the planned-workout calendar
    spanning the pull window. Enables planned-vs-actual coaching."""
    from garmin_coach.ingest import transforms as T

    try:
        plan = T.training_plan_row(client.call("get_training_plans"))
        if plan and plan.get("plan_id"):
            db.upsert(conn, "training_plan", plan, pk=["plan_id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("training plan failed: %s", exc)

    try:
        n = 0
        for w in T.workout_rows(client.call("get_workouts")):
            db.upsert(conn, "workouts", w, pk=["workout_id"])
            n += 1
        log.info("ingested %d workout templates", n)
    except Exception as exc:  # noqa: BLE001
        log.warning("workouts failed: %s", exc)

    # Planned calendar: iterate each (year, month) in the window + a look-ahead.
    try:
        months = _months_between(start, end, look_ahead=2)
        total = 0
        for yr, mo in months:
            cal = client.call("get_scheduled_workouts", yr, mo)
            for pw in T.planned_workout_rows(cal):
                db.upsert(conn, "planned_workouts", pw, pk=["id"])
                total += 1
        log.info("ingested %d planned workouts across %d months", total, len(months))
    except Exception as exc:  # noqa: BLE001
        log.warning("planned workouts failed: %s", exc)
    conn.commit()


def _months_between(start: str, end: str, look_ahead: int = 0):
    """List of (year, month) tuples from start to end + look_ahead extra months."""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    em += look_ahead
    while em > 12:
        em -= 12
        ey += 1
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _ingest_prs(client, conn) -> None:
    from garmin_coach.ingest import transforms as T

    try:
        raw = client.call("get_personal_record")
        n = 0
        for row in T.pr_rows(raw):
            db.upsert(conn, "personal_records", row, pk=["pr_id"])
            n += 1
        conn.commit()
        log.info("ingested %d personal records", n)
    except Exception as exc:  # noqa: BLE001
        log.warning("personal records failed: %s", exc)


_WELLNESS_DATASETS = ["sleep", "hrv", "stress", "body_battery", "rhr", "readiness",
                      "training_status", "daily_stats", "fitness", "recovery",
                      "intraday_hr"]


def _all_done(conn, date) -> bool:
    return all(
        db.date_done(conn, date, ds) or _is_missing(conn, date, ds)
        for ds in _WELLNESS_DATASETS
    )


def _is_missing(conn, date, ds) -> bool:
    row = conn.execute(
        "SELECT status FROM ingest_log WHERE date=? AND dataset=?", (date, ds)
    ).fetchone()
    return bool(row and row["status"] == "missing")


def main() -> int:
    p = argparse.ArgumentParser(description="Pull Garmin data into SQLite.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--nightly", action="store_true", help="Re-pull trailing days + recent activities.")
    g.add_argument("--date", metavar="YYYY-MM-DD", help="Pull a single day.")
    g.add_argument("--backfill", type=int, metavar="N", help="Pull the last N days.")
    p.add_argument("--no-streams", action="store_true", help="Skip per-second activity streams.")
    args = p.parse_args()

    with_streams = not args.no_streams
    if args.nightly:
        return run("nightly", 0, None, with_streams)
    if args.date:
        return run("date", 0, args.date, with_streams)
    return run("backfill", args.backfill, None, with_streams)


if __name__ == "__main__":
    sys.exit(main())
