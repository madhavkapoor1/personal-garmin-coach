"""Pull activities in a date range: summary + splits + time-in-zone, with
optional per-second streams (opt-in for runs only, storage-heavy).

Decoupling is computed at ingest from the streams when available; see
analytics.metrics.decoupling_from_streams.
"""
from __future__ import annotations

import logging
import sqlite3

from garmin_coach import db
from garmin_coach.analytics import metrics
from garmin_coach.ingest import transforms as T
from garmin_coach.ingest.client import Client

log = logging.getLogger(__name__)

RUN_TYPES = {"running", "trail_running", "treadmill_running", "track_running"}
STRENGTH_TYPES = {"strength_training", "indoor_cardio", "hiit"}


def ingest_range(
    client: Client,
    conn: sqlite3.Connection,
    start: str,
    end: str,
    with_streams: bool = True,
) -> int:
    """Pull every activity between start and end (inclusive). Returns count."""
    try:
        acts = client.call("get_activities_by_date", start, end)
    except Exception as exc:  # noqa: BLE001
        log.error("Listing activities %s..%s failed: %s", start, end, exc)
        db.log_ingest(conn, start, "activities_list", "error", str(exc))
        return 0

    acts = acts or []
    n = 0
    for a in acts:
        row = T.activity_row(a)
        if not row:
            continue
        aid = row["activity_id"]
        atype = row.get("type") or ""
        is_run = atype in RUN_TYPES
        try:
            _enrich(client, conn, aid, row, is_run and with_streams)
            # Weather (all outdoor activities may have it)
            try:
                row.update(T.weather_fields(client.call("get_activity_weather", aid)))
            except Exception as exc:  # noqa: BLE001
                log.debug("weather %s: %s", aid, exc)
            # Strength sets (exercise / reps / weight)
            if atype in STRENGTH_TYPES:
                try:
                    sets = client.call("get_activity_exercise_sets", aid)
                    for sr in T.strength_set_rows(aid, sets):
                        db.upsert(conn, "strength_sets", sr, pk=["activity_id", "set_idx"])
                except Exception as exc:  # noqa: BLE001
                    log.debug("exercise_sets %s: %s", aid, exc)
            db.upsert(conn, "activities", row, pk=["activity_id"])
            conn.commit()
            n += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("Activity %s failed: %s", aid, exc)
            db.log_ingest(conn, row.get("date") or start, f"activity:{aid}", "error", str(exc))
    if n:
        db.log_ingest(conn, start, "activities_list", "ok", f"{n} activities")
    return n


def _enrich(client, conn, aid, row, want_streams):
    """Add splits, time-in-zone, streams, and decoupling to a run."""
    # Splits
    try:
        splits = client.call("get_activity_splits", aid)
        for s in T.split_rows(aid, splits):
            db.upsert(conn, "activity_splits", s, pk=["activity_id", "split_idx"])
    except Exception as exc:  # noqa: BLE001
        log.debug("splits %s: %s", aid, exc)

    # Time-in-zone + dynamic zone boundaries
    try:
        zones = client.call("get_activity_hr_in_timezones", aid)
        row.update(T.zone_seconds(zones))
        bounds = T.zone_bounds(zones)
        if bounds and row.get("date"):
            bounds.update({"date": row["date"], "activity_id": aid})
            db.upsert(conn, "hr_zones", bounds, pk=["date"])
    except Exception as exc:  # noqa: BLE001
        log.debug("zones %s: %s", aid, exc)

    # Per-second streams + decoupling (runs only)
    if want_streams:
        try:
            detail = client.call("get_activity_details", aid, maxchart=2000, maxpoly=0)
            stream_rows = list(_stream_rows(aid, detail))
            for sr in stream_rows:
                db.upsert(conn, "activity_streams", sr, pk=["activity_id", "offset_s"])
            row["decoupling_pct"] = metrics.decoupling_from_streams(stream_rows)
        except Exception as exc:  # noqa: BLE001
            log.debug("streams %s: %s", aid, exc)


def _stream_rows(aid, detail):
    """Map get_activity_details metric arrays into per-second stream rows."""
    metrics_arr = (detail or {}).get("activityDetailMetrics") or []
    descriptors = (detail or {}).get("metricDescriptors") or []
    # Build key -> column index from descriptors.
    idx = {}
    for d in descriptors:
        key = (d.get("key") or "").lower()
        idx[key] = d.get("metricsIndex")

    def col(names):
        for nm in names:
            if nm in idx and idx[nm] is not None:
                return idx[nm]
        return None

    i_hr = col(["directheartrate", "heartrate"])
    i_speed = col(["directspeed", "speed"])
    i_cad = col(["directruncadence", "directcadence", "runcadence"])
    i_alt = col(["directelevation", "elevation"])
    i_pow = col(["directpower", "power"])
    i_lat = col(["directlatitude", "latitude"])
    i_lon = col(["directlongitude", "longitude"])
    i_t = col(["directtimestamp", "sumelapsedduration", "directelapsedduration"])

    t0 = None
    for offset, m in enumerate(metrics_arr):
        vals = m.get("metrics") or []

        def v(i):
            return vals[i] if (i is not None and i < len(vals)) else None

        # Prefer a real elapsed-time column; else use row index as seconds.
        raw_t = v(i_t)
        if raw_t is not None:
            if t0 is None:
                t0 = raw_t
            off = int((raw_t - t0) / 1000) if raw_t > 1e10 else int(raw_t - t0)
        else:
            off = offset
        yield {
            "activity_id": aid,
            "offset_s": off,
            "hr": _int(v(i_hr)),
            "speed_mps": v(i_speed),
            "cadence": v(i_cad),
            "altitude_m": v(i_alt),
            "power": v(i_pow),
            "lat": v(i_lat),
            "lon": v(i_lon),
        }


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
