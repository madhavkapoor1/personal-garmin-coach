"""Garmin Coach MCP server (stdio).

Exposes the local SQLite store to Claude Code / Claude Desktop as read-only
tools so Claude can act as your running coach — free within your Claude
subscription, no API key, no per-token cost.

Safety: the DB is opened READ-ONLY and run_sql rejects anything that isn't a
single SELECT/WITH, so the model can never mutate or drop your history.

Register with Claude Code:
    claude mcp add garmin-coach -- <path-to-venv-python> -m garmin_coach.mcp.server
Or add to Claude Desktop's config (see README).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# Make project root importable when launched as a module by the MCP host.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import config  # noqa: E402
from garmin_coach import db  # noqa: E402
from garmin_coach.analytics import metrics  # noqa: E402

mcp = FastMCP(
    "garmin-coach",
    instructions=(
        "You are the athlete's running coach with read-only access to their Garmin "
        "history (runs, sleep, HRV, stress, Body Battery, resting HR, training "
        "readiness/status). Reason about training load (ACWR), recovery trends, "
        "aerobic fitness (pace at a fixed HR, decoupling), and the 80/20 easy/hard "
        "split. You also have VO2max & predicted race times (fitness table), "
        "per-set strength data (strength_sets: exercise_name, reps, weight_kg), "
        "personal records, and per-run weather (activities.temp_c/humidity). "
        "Call get_hr_zones for the athlete's CURRENT zones (pulled live, not "
        "hardcoded) before judging easy/hard intensity. Call get_training_plan "
        "to see their goal race, upcoming planned sessions, and planned-vs-actual "
        "adherence — coach toward that plan and flag missed quality work. "
        "Call get_schema first if you need column names, then run_sql for custom "
        "analysis. Always quote the real numbers you pulled, distinguish missing "
        "data from zero, and give specific, physiologically-sound, safe advice — "
        "protect long-term progress over short-term heroics."
    ),
)

_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|pragma|vacuum)\b",
    re.IGNORECASE,
)


def _ro():
    return db.connect_ro()


def _fmt_pace(s):
    """Seconds/km -> 'mm:ss/km' string. None-safe."""
    if s is None:
        return None
    m, sec = divmod(int(round(s)), 60)
    return f"{m}:{sec:02d}/km"


def _rows(cur) -> list[dict]:
    """Fetch rows as dicts, adding a mm:ss '<field>_min_km' next to any
    '*_s_per_km' pace column so Claude reports pace in min/km, not raw seconds."""
    out = []
    for r in cur.fetchall():
        d = dict(r)
        for k in list(d.keys()):
            if k.endswith("_s_per_km"):
                d[k.replace("_s_per_km", "_min_km")] = _fmt_pace(d[k])
        out.append(d)
    return out


@mcp.tool()
def get_schema() -> str:
    """Return the SQL DDL of every table so you know the columns before querying."""
    with _ro() as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
            "ORDER BY name"
        ).fetchall()
    return "\n\n".join(r[0] for r in rows)


@mcp.tool()
def run_sql(query: str, limit: int = 500) -> str:
    """Run a single read-only SELECT/WITH query against the Garmin DB.

    Non-SELECT statements are rejected. A LIMIT is enforced if you omit one.
    Returns JSON rows. Use get_schema() first for exact column names.
    """
    if not _SELECT_ONLY.match(query) or _FORBIDDEN.search(query) or ";" in query.strip().rstrip(";"):
        return json.dumps({"error": "Only a single read-only SELECT/WITH query is allowed."})
    q = query.strip().rstrip(";")
    if "limit" not in q.lower():
        q = f"{q}\nLIMIT {int(limit)}"
    try:
        with _ro() as conn:
            return json.dumps(_rows(conn.execute(q)), default=str)
    except sqlite3.Error as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_recent_metrics(days: int = 14) -> str:
    """Joined daily snapshot (sleep, HRV, RHR, stress, Body Battery, readiness,
    training status, daily load) for the last `days` days. The 'how am I doing
    lately' overview."""
    sql = """
        SELECT d.date,
               sl.sleep_score, sl.total_sleep_s/3600.0 AS sleep_h,
               h.last_night_avg AS hrv, h.status AS hrv_status,
               r.resting_hr, st.avg_stress, bb.high AS bb_high, bb.low AS bb_low,
               rd.score AS readiness, ts.status AS training_status, ts.vo2max,
               (SELECT COALESCE(SUM(COALESCE(training_load,distance_m/1000.0)),0)
                  FROM activities a WHERE a.date = d.date) AS day_load
        FROM (SELECT date FROM sleep UNION SELECT date FROM hrv
              UNION SELECT date FROM rhr UNION SELECT date FROM activities) d
        LEFT JOIN sleep sl ON sl.date=d.date
        LEFT JOIN hrv h ON h.date=d.date
        LEFT JOIN rhr r ON r.date=d.date
        LEFT JOIN stress st ON st.date=d.date
        LEFT JOIN body_battery bb ON bb.date=d.date
        LEFT JOIN readiness rd ON rd.date=d.date
        LEFT JOIN training_status ts ON ts.date=d.date
        WHERE d.date >= date('now', ?)
        ORDER BY d.date DESC
    """
    with _ro() as conn:
        return json.dumps(_rows(conn.execute(sql, (f"-{int(days)} days",))), default=str)


@mcp.tool()
def list_activities(start: str, end: str, type: str = "running", limit: int = 50) -> str:
    """Activity summaries between start and end (YYYY-MM-DD). `type` matches a
    substring of the activity type (e.g. 'running'); use '' for all types."""
    with _ro() as conn:
        cur = conn.execute(
            """SELECT activity_id, date, name, type, distance_m/1000.0 km,
                      avg_pace_s_per_km, avg_hr, max_hr, training_load,
                      decoupling_pct, z1_s,z2_s,z3_s,z4_s,z5_s
               FROM activities
               WHERE date BETWEEN ? AND ? AND type LIKE ?
               ORDER BY date DESC LIMIT ?""",
            (start, end, f"%{type}%", int(limit)),
        )
        return json.dumps(_rows(cur), default=str)


@mcp.tool()
def get_activity_detail(activity_id: int, include_streams: bool = False) -> str:
    """Full summary + splits + time-in-zone for one activity. If include_streams,
    also returns the per-second HR/pace stream DOWNSAMPLED to ~120 points so it
    never floods the context."""
    with _ro() as conn:
        act = conn.execute(
            "SELECT * FROM activities WHERE activity_id=?", (activity_id,)
        ).fetchone()
        if not act:
            return json.dumps({"error": "activity not found"})
        act = dict(act)
        act.pop("raw_json", None)
        if act.get("avg_pace_s_per_km") is not None:
            act["avg_pace_min_km"] = _fmt_pace(act["avg_pace_s_per_km"])
        splits = _rows(conn.execute(
            "SELECT split_idx, distance_m, duration_s, avg_pace_s_per_km, avg_hr "
            "FROM activity_splits WHERE activity_id=? ORDER BY split_idx", (activity_id,)))
        out = {"activity": act, "splits": splits}
        if include_streams:
            total = conn.execute(
                "SELECT COUNT(*) c FROM activity_streams WHERE activity_id=?",
                (activity_id,)).fetchone()["c"]
            step = max(1, total // 120)
            stream = _rows(conn.execute(
                "SELECT offset_s, hr, speed_mps, altitude_m FROM activity_streams "
                "WHERE activity_id=? AND offset_s % ? = 0 ORDER BY offset_s",
                (activity_id, step * 5)))  # streams stored at 5s in sample; harmless otherwise
            out["streams_downsampled"] = stream
    return json.dumps(out, default=str)


@mcp.tool()
def get_training_load(as_of: str = "") -> str:
    """Acute (7d) vs chronic (28d avg) load, ACWR ratio and a LOW/OPTIMAL/
    ELEVATED/HIGH flag, plus Garmin's own load_ratio. `as_of` optional YYYY-MM-DD."""
    with _ro() as conn:
        result = metrics.acwr(conn, as_of or None)
        g = conn.execute(
            "SELECT load_ratio, status FROM training_status "
            "WHERE date <= COALESCE(NULLIF(?, ''), date('now')) AND load_ratio IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", (as_of,)).fetchone()
        if g:
            result["garmin_load_ratio"] = g["load_ratio"]
            result["garmin_status"] = g["status"]
        result["zone_distribution_28d"] = metrics.zone_distribution(conn, 28)
    return json.dumps(result, default=str)


@mcp.tool()
def get_pace_at_hr(hr_low: int, hr_high: int, months: int = 6) -> str:
    """Month-by-month average running pace within an HR band. Falling pace at the
    same HR = improving aerobic fitness. Pick a Zone-2 band for the cleanest read."""
    with _ro() as conn:
        rows = metrics.pace_at_hr(conn, int(hr_low), int(hr_high), int(months))
    for r in rows:
        r["avg_pace_min_km"] = _fmt_pace(r.get("avg_pace_s_per_km"))
    return json.dumps(rows)


@mcp.tool()
def get_training_plan(days_ahead: int = 10, days_back: int = 14) -> str:
    """The athlete's active training plan (goal + timeline), the upcoming planned
    sessions, and recent planned-vs-actual adherence. Use this to coach toward
    the goal race, flag missed sessions, and place upcoming quality work."""
    with _ro() as conn:
        plan = conn.execute("SELECT * FROM training_plan LIMIT 1").fetchone()
        upcoming = _rows(conn.execute(
            "SELECT date, title, sport FROM planned_workouts "
            "WHERE date >= date('now') AND date <= date('now', ?) ORDER BY date",
            (f"+{int(days_ahead)} days",)))
        recent = _rows(conn.execute(
            """SELECT p.date, p.title AS planned,
                      (SELECT ROUND(a.distance_m/1000.0,1) FROM activities a
                       WHERE a.date=p.date AND a.type LIKE '%running%' LIMIT 1) AS actual_km
               FROM planned_workouts p
               WHERE p.sport='running' AND p.date < date('now')
                     AND p.date >= date('now', ?) ORDER BY p.date DESC""",
            (f"-{int(days_back)} days",)))
    return json.dumps({"plan": dict(plan) if plan else None,
                       "upcoming": upcoming, "recent_planned_vs_actual": recent}, default=str)


@mcp.tool()
def get_hr_zones() -> str:
    """The athlete's CURRENT HR zones, pulled live from their Garmin data (not
    hardcoded): zone boundaries, easy ceiling (top of Z2), threshold HR (LTHR),
    and observed max HR. Use the easy ceiling to judge whether easy runs are
    actually easy, and LTHR to anchor threshold work."""
    with _ro() as conn:
        return json.dumps(metrics.effective_zones(conn), default=str)


@mcp.tool()
def get_fitness_snapshot() -> str:
    """Latest VO2max, fitness age, and predicted race times (5K/10K/half/marathon),
    plus how VO2max has trended. The 'how fit am I right now' read."""
    with _ro() as conn:
        latest = conn.execute(
            "SELECT * FROM fitness WHERE vo2max_precise IS NOT NULL OR race_5k_s IS NOT NULL "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        trend = _rows(conn.execute(
            "SELECT date, vo2max_precise FROM fitness WHERE vo2max_precise IS NOT NULL "
            "ORDER BY date"))
    return json.dumps({"latest": dict(latest) if latest else None,
                       "vo2max_trend": trend}, default=str)


@mcp.tool()
def get_strength_progress(exercise: str = "") -> str:
    """Strength training summary. With no arg, lists each exercise with sets done,
    top weight, and last date. With an exercise name (e.g. 'DUMBBELL_BENCH_PRESS'),
    returns its per-session weight/reps progression."""
    with _ro() as conn:
        if exercise:
            rows = _rows(conn.execute(
                """SELECT a.date, MAX(s.weight_kg) top_weight_kg, MAX(s.reps) max_reps,
                          COUNT(*) sets
                   FROM strength_sets s JOIN activities a ON a.activity_id=s.activity_id
                   WHERE s.exercise_name=? AND s.set_type='ACTIVE'
                   GROUP BY a.date ORDER BY a.date""", (exercise,)))
        else:
            rows = _rows(conn.execute(
                """SELECT exercise_name, COUNT(*) sets, MAX(weight_kg) top_weight_kg,
                          MAX(reps) max_reps
                   FROM strength_sets WHERE exercise_name IS NOT NULL AND set_type='ACTIVE'
                   GROUP BY exercise_name ORDER BY sets DESC"""))
    return json.dumps(rows, default=str)


@mcp.prompt()
def weekly_review() -> str:
    """A Monday-review prompt: summarize the week behind in numbers and shape the week ahead."""
    return (
        "Run my weekly review. Use get_recent_metrics(14), get_training_load(), and "
        "get_pace_at_hr() around my Zone-2 ceiling, plus list_activities() for the last 7 days.\n\n"
        "PART 1 — the week behind: total load vs my 4-week average; easy/hard split and whether "
        "easy was actually easy; sleep & HRV trend; did key sessions hit target?\n"
        "PART 2 — the week ahead: given recovery state, what should this week prioritise? Give a "
        "day-by-day skeleton (hard/easy/long/rest). If recovery says ease off, override and say so.\n"
        "Quote real numbers. Keep it scannable."
    )


def main():
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
