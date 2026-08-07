"""Pull one day of every daily-wellness metric and UPSERT it.

Each metric is independent: a failure or a gap in one (say HRV) never blocks the
others, and each writes its own ingest_log row (ok / missing / error).
"""
from __future__ import annotations

import logging
import sqlite3

from garmin_coach import db
from garmin_coach.ingest import transforms as T
from garmin_coach.ingest.client import Client

log = logging.getLogger(__name__)

# (dataset name, Garmin method, args-builder, transform, table, pk)
_SPECS = [
    ("sleep", "get_sleep_data", lambda d: (d,), T.sleep_row, "sleep"),
    ("hrv", "get_hrv_data", lambda d: (d,), T.hrv_row, "hrv"),
    ("stress", "get_stress_data", lambda d: (d,), T.stress_row, "stress"),
    ("body_battery", "get_body_battery", lambda d: (d, d), T.body_battery_row, "body_battery"),
    ("rhr", "get_rhr_day", lambda d: (d,), T.rhr_row, "rhr"),
    ("readiness", "get_training_readiness", lambda d: (d,), T.readiness_row, "readiness"),
    ("training_status", "get_training_status", lambda d: (d,), T.training_status_row, "training_status"),
    ("daily_stats", "get_user_summary", lambda d: (d,), T.daily_stats_row, "daily_stats"),
]


def ingest_day(client: Client, conn: sqlite3.Connection, date: str) -> dict:
    """Pull all wellness metrics for `date`. Returns {dataset: status}."""
    results: dict[str, str] = {}
    for dataset, method, argfn, transform, table in _SPECS:
        try:
            raw = client.call(method, *argfn(date))
            row = transform(date, raw)
            if row is None:
                db.log_ingest(conn, date, dataset, "missing")
                results[dataset] = "missing"
            else:
                db.upsert(conn, table, row, pk=["date"])
                db.log_ingest(conn, date, dataset, "ok")
                results[dataset] = "ok"
        except Exception as exc:  # noqa: BLE001 - isolate per-metric failures
            log.warning("%s %s failed: %s", date, dataset, exc)
            db.log_ingest(conn, date, dataset, "error", str(exc))
            results[dataset] = "error"
        conn.commit()

    results["fitness"] = _ingest_fitness(client, conn, date)
    results["recovery"] = _ingest_recovery(client, conn, date)
    results["intraday_hr"] = _ingest_intraday_hr(client, conn, date)
    conn.commit()
    return results


def _safe(client, method, *args):
    """Call a Garmin method, returning None on any failure (optional metrics)."""
    try:
        return client.call(method, *args)
    except Exception:  # noqa: BLE001
        return None


def _ingest_fitness(client, conn, date) -> str:
    """VO2max, race predictions, fitness age, endurance & hill scores."""
    try:
        row = T.fitness_row(
            date,
            _safe(client, "get_max_metrics", date),
            _safe(client, "get_race_predictions"),
            _safe(client, "get_fitnessage_data", date),
            _safe(client, "get_endurance_score", date, date),
            _safe(client, "get_hill_score", date, date),
        )
        # Running-power FTP (single latest value; attach to the day's row)
        ftp = T.ftp_fields(_safe(client, "get_lactate_threshold", ))
        if row is None and any(ftp.values()):
            row = {"date": date}
        if row is not None:
            row.update({k: v for k, v in ftp.items() if v is not None})
        if row is None:
            db.log_ingest(conn, date, "fitness", "missing")
            return "missing"
        db.upsert(conn, "fitness", row, pk=["date"])
        db.log_ingest(conn, date, "fitness", "ok")
        return "ok"
    except Exception as exc:  # noqa: BLE001
        db.log_ingest(conn, date, "fitness", "error", str(exc))
        return "error"


def _ingest_intraday_hr(client, conn, date) -> str:
    """All-day HR curve, downsampled to ~5-min cadence (lean, chart-friendly)."""
    try:
        raw = _safe(client, "get_heart_rates", date)
        rows = list(T.intraday_hr_rows(raw, date))
        if not rows:
            db.log_ingest(conn, date, "intraday_hr", "missing")
            return "missing"
        for r in rows:
            db.upsert(conn, "intraday_hr", r, pk=["date", "offset_s"])
        db.log_ingest(conn, date, "intraday_hr", "ok")
        return "ok"
    except Exception as exc:  # noqa: BLE001
        db.log_ingest(conn, date, "intraday_hr", "error", str(exc))
        return "error"


def _ingest_recovery(client, conn, date) -> str:
    """SpO2 / respiration / hydration → merged onto the daily_stats row."""
    try:
        fields = T.recovery_fields(
            _safe(client, "get_spo2_data", date),
            _safe(client, "get_respiration_data", date),
            _safe(client, "get_hydration_data", date),
        )
        if all(v is None for v in fields.values()):
            db.log_ingest(conn, date, "recovery", "missing")
            return "missing"
        fields["date"] = date
        db.upsert(conn, "daily_stats", fields, pk=["date"])
        db.log_ingest(conn, date, "recovery", "ok")
        return "ok"
    except Exception as exc:  # noqa: BLE001
        db.log_ingest(conn, date, "recovery", "error", str(exc))
        return "error"
