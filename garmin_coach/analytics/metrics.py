"""Training analytics used by the dashboard, the MCP server, and (later) the
Level-5 agent. Pure functions over the SQLite store or over raw stream rows, so
every surface computes fitness the same way.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional, Sequence

import config


def effective_zones(conn: sqlite3.Connection) -> dict:
    """Resolve the athlete's current HR zones, preferring live Garmin data over
    static config so they track fitness changes automatically.

    Source of truth: the most recent `hr_zones` row (boundaries Garmin applied to
    the latest activity). Max HR = highest observed across all activities. The
    .env values are used only as a fallback, or as a hard override when the env
    var is explicitly set (HR_ZONE2_CEILING etc.).
    """
    row = conn.execute(
        "SELECT z1_low,z2_low,z3_low,z4_low,z5_low,date FROM hr_zones "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    obs_max = conn.execute("SELECT MAX(max_hr) FROM activities").fetchone()[0]

    if row and row["z3_low"]:
        z2_ceiling = row["z3_low"] - 1          # top of easy = just below Z3
        lthr = row["z4_low"]                    # threshold zone start
        zones = {f"z{i}_low": row[f"z{i}_low"] for i in range(1, 6)}
        source = f"garmin (as of {row['date']})"
    else:
        z2_ceiling, lthr, zones = config.HR_ZONE2_CEILING, config.HR_LTHR, {}
        source = "config fallback"

    max_hr = obs_max or config.HR_MAX
    # Explicit env vars act as a manual override if the user sets them.
    if os.getenv("HR_ZONE2_CEILING"):
        z2_ceiling = config.HR_ZONE2_CEILING
    if os.getenv("HR_LTHR"):
        lthr = config.HR_LTHR
    if os.getenv("HR_MAX"):
        max_hr = config.HR_MAX

    return {"z2_ceiling": z2_ceiling, "lthr": lthr, "max_hr": max_hr,
            "boundaries": zones, "source": source}


# --- decoupling (Pa:HR drift, first half vs second half) ---------------------
def decoupling_from_streams(stream_rows: Sequence[dict]) -> Optional[float]:
    """Aerobic decoupling %: how much pace-per-heartbeat drifts from the first
    half of a run to the second. <5% on an easy run = strong aerobic base.

    Efficiency = speed / HR. Decoupling = (eff_first - eff_second)/eff_first*100.
    A positive number means you slowed (or HR rose) in the second half.
    """
    pts = [
        (r.get("speed_mps"), r.get("hr"))
        for r in stream_rows
        if r.get("speed_mps") and r.get("hr")
    ]
    if len(pts) < 20:
        return None
    mid = len(pts) // 2
    first, second = pts[:mid], pts[mid:]

    def eff(chunk):
        spd = sum(p[0] for p in chunk) / len(chunk)
        hr = sum(p[1] for p in chunk) / len(chunk)
        return spd / hr if hr else None

    e1, e2 = eff(first), eff(second)
    if not e1 or not e2:
        return None
    return round((e1 - e2) / e1 * 100.0, 1)


# --- acute:chronic workload ratio (ACWR) -------------------------------------
def acwr(conn: sqlite3.Connection, as_of: Optional[str] = None) -> dict:
    """Acute (7d) vs chronic (28d avg) training load ratio.

    Safe zone is roughly 0.8–1.3; above ~1.5 flags spiking load / injury risk.
    Uses per-activity `training_load`; falls back to distance if load is absent.
    """
    # :as_of is always bound (NULL when not given) so COALESCE picks 'now'.
    where_date = "date <= COALESCE(:as_of, date('now'))"
    params = {"as_of": as_of}
    load_expr = "COALESCE(training_load, distance_m/1000.0)"

    acute = conn.execute(
        f"""SELECT COALESCE(SUM({load_expr}),0) FROM activities
            WHERE {where_date} AND date >= date(COALESCE(:as_of,'now'), '-6 days')""",
        params,
    ).fetchone()[0]
    chronic_total = conn.execute(
        f"""SELECT COALESCE(SUM({load_expr}),0) FROM activities
            WHERE {where_date} AND date >= date(COALESCE(:as_of,'now'), '-27 days')""",
        params,
    ).fetchone()[0]
    chronic_weekly = chronic_total / 4.0
    ratio = round(acute / chronic_weekly, 2) if chronic_weekly else None
    if ratio is None:
        flag = "UNKNOWN"
    elif ratio < 0.8:
        flag = "LOW"
    elif ratio <= 1.3:
        flag = "OPTIMAL"
    elif ratio <= 1.5:
        flag = "ELEVATED"
    else:
        flag = "HIGH"
    return {
        "acute_7d": round(acute, 1),
        "chronic_weekly_avg": round(chronic_weekly, 1),
        "acwr": ratio,
        "flag": flag,
    }


# --- pace at a fixed HR band, month over month -------------------------------
def pace_at_hr(
    conn: sqlite3.Connection, hr_low: int, hr_high: int, months: int = 6
) -> list[dict]:
    """Avg running pace within an HR band, grouped by month. Falling pace at the
    same HR = improving aerobic fitness (the headline trend chart)."""
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', date) AS month,
               ROUND(AVG(avg_pace_s_per_km),1) AS avg_pace_s_per_km,
               COUNT(*) AS runs
        FROM activities
        WHERE type LIKE '%running%'
          AND avg_hr BETWEEN ? AND ?
          AND avg_pace_s_per_km IS NOT NULL
          AND date >= date('now', ?)
        GROUP BY month ORDER BY month
        """,
        (hr_low, hr_high, f"-{months} months"),
    ).fetchall()
    return [dict(r) for r in rows]


# --- zone distribution (80/20 check) -----------------------------------------
def zone_distribution(conn: sqlite3.Connection, days: int = 28) -> dict:
    """Total seconds in each HR zone over the window + easy/hard split."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(z1_s),0) z1, COALESCE(SUM(z2_s),0) z2,
               COALESCE(SUM(z3_s),0) z3, COALESCE(SUM(z4_s),0) z4,
               COALESCE(SUM(z5_s),0) z5
        FROM activities WHERE date >= date('now', ?)
        """,
        (f"-{days} days",),
    ).fetchone()
    z = {k: row[k] for k in ("z1", "z2", "z3", "z4", "z5")}
    total = sum(z.values())
    easy = z["z1"] + z["z2"]
    hard = z["z3"] + z["z4"] + z["z5"]
    return {
        "seconds": z,
        "easy_pct": round(easy / total * 100, 1) if total else None,
        "hard_pct": round(hard / total * 100, 1) if total else None,
        "total_min": round(total / 60) if total else 0,
    }
