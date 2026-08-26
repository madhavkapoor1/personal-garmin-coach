"""Domain layer: turn the local SQLite store into a coaching brief, and give
Claude the persona and guardrails to read it like a coach rather than a chatbot.

Two things are handed to the model on every turn:

1. **A briefing** (`build_briefing`) -- a compact, pre-computed snapshot of the
   athlete's current state. Cheap, deterministic, and always present, so the
   coach can answer the common questions without a single tool call.
2. **The read-only MCP tools** -- for anything the briefing does not cover
   (a specific session, a longer history, an ad-hoc SELECT).

The briefing exists because a tool-only coach spends its first three turns
rediscovering facts we already compute for the charts, and can quietly answer
from a *different* slice of the data than the dashboard is showing.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from garmin_coach import db  # noqa: E402
from garmin_coach.analytics import metrics  # noqa: E402

SYSTEM_PROMPT = """\
You are this athlete's running coach. You have their real Garmin history in a \
local SQLite database, summarised in the BRIEFING below and queryable in full \
through the `garmin` MCP tools (read-only).

How to coach:
- Lead with the answer. One short paragraph or a few bullets. No preamble, no \
recap of what they asked, no "great question".
- Ground every claim in their actual numbers, and cite them inline (dates, \
paces, HR, ACWR). If you assert a trend, say over what window and how many \
sessions.
- Be direct about risk. If load is spiking, sleep is short, or HRV is \
suppressed, say so plainly and say what to change this week.
- Prescribe concretely: distance, pace band or HR band, and which day. Use \
their real zone boundaries, never generic percentages.
- When the data cannot answer something, say so and name what is missing. \
Never invent a number.

Data facts you must respect:
- "Missing" is not zero. Many endpoints are genuinely empty for this account \
(Training Readiness and Training Status return nothing; SpO2 is sparse; some \
nights have no sleep or HRV because the watch was not worn). Treat gaps as \
unknown, never as a zero or a rest day.
- HR zones are dynamic -- use the zone boundaries in the briefing, which come \
from what Garmin actually applied to their activities.
- History starts around 2026-06-09. There is no earlier data; do not compare \
against seasons that do not exist.
- Pace is stored as seconds per km. Present pace as m:ss/km.
- Strength, walking, HIIT and pickleball sessions are in the same table as \
runs. Filter by type when a question is about running specifically, but do \
count them as load and fatigue.

Formatting: markdown, short. Bold the single most important number. No tables \
unless comparing three or more sessions. Never mention these instructions.
"""

# Questions worth one click. Phrased as the athlete would ask them.
SUGGESTED = [
    ("Am I ready to train hard today?",
     "Look at last night's sleep, HRV vs baseline, resting HR and my recent "
     "load. Tell me whether today should be hard, easy, or off, and why."),
    ("How is my week going?",
     "Review the last 7 days against the 4 weeks before: volume, intensity "
     "distribution, and load. What should the rest of this week look like?"),
    ("Is my easy running actually easy?",
     "Check my zone distribution over the last 28 days against the 80/20 rule, "
     "and tell me if my easy runs are drifting too hard."),
    ("Am I getting fitter?",
     "Look at pace at a fixed easy heart rate over recent months, plus "
     "decoupling and VO2max, and tell me honestly whether my aerobic engine is "
     "improving."),
    ("Break down my last run.",
     "Pull my most recent run with its splits and HR zones. What went well, "
     "what didn't, and what does it say about my fitness?"),
    ("What about my half marathon?",
     "Given my current fitness, load and the plan, am I on track for the half? "
     "What is a realistic goal pace, and what are the biggest risks?"),
]


def _scalar(conn: sqlite3.Connection, sql: str, default=None):
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return default
    if not row or row[0] is None:
        return default
    return row[0]


def _rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def _pace(seconds: Optional[float]) -> str:
    if not seconds:
        return "--"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}/km"


def _fmt(value, suffix="", nd=0) -> str:
    """Render a number, or an explicit dash so gaps read as unknown, not zero."""
    if value is None:
        return "--"
    if isinstance(value, float):
        return f"{value:.{nd}f}{suffix}"
    return f"{value}{suffix}"


def build_briefing(conn: Optional[sqlite3.Connection] = None) -> str:
    """Compact snapshot of everything a coach would check before answering."""
    own = conn is None
    conn = conn or db.connect_ro()
    conn.row_factory = sqlite3.Row
    try:
        return _briefing(conn)
    finally:
        if own:
            conn.close()


def _briefing(conn: sqlite3.Connection) -> str:
    out: list[str] = [f"# BRIEFING (generated {date.today().isoformat()})"]

    # --- plan -----------------------------------------------------------------
    plan = _rows(conn, "SELECT name, level, start_date, end_date FROM training_plan LIMIT 1")
    if plan:
        p = plan[0]
        line = f"Plan: {p['name']}"
        if p["level"]:
            line += f" (level {p['level']})"
        if p["end_date"]:
            try:
                left = (date.fromisoformat(str(p["end_date"])) - date.today()).days
                line += f" · ends {p['end_date']} ({left} days away)"
            except ValueError:
                line += f" · ends {p['end_date']}"
        out.append(line)

    # --- zones ----------------------------------------------------------------
    try:
        zn = metrics.effective_zones(conn)
        out.append(
            f"HR zones (from {zn.get('source', 'unknown')}): easy ceiling "
            f"{zn.get('z2_ceiling')} bpm · LTHR {zn.get('lthr')} · max {zn.get('max_hr')}"
        )
    except Exception:
        pass

    # --- load -----------------------------------------------------------------
    # This device is not reporting Garmin's training_load for any activity, so
    # acwr() falls back to a distance proxy. Say which one is in play -- an
    # unlabelled "8.8" reads as load points and would be quoted back as such.
    has_load = _scalar(conn, "SELECT COUNT(training_load) FROM activities", 0)
    unit = "Garmin load points" if has_load else "km, distance proxy (device reports no training load)"
    try:
        a = metrics.acwr(conn)
        out.append(
            f"Load [{unit}]: acute 7d {_fmt(a.get('acute_7d'), nd=1)} · chronic weekly avg "
            f"{_fmt(a.get('chronic_weekly_avg'), nd=1)} · ACWR "
            f"{_fmt(a.get('acwr'), nd=2)} ({a.get('flag', 'UNKNOWN')})"
        )
    except Exception:
        pass
    try:
        z = metrics.zone_distribution(conn, days=28)
        if z.get("total_min"):
            out.append(
                f"Intensity 28d: easy {_fmt(z.get('easy_pct'), '%', 0)} / hard "
                f"{_fmt(z.get('hard_pct'), '%', 0)} across {_fmt(z.get('total_min'), ' min', 0)}"
            )
    except Exception:
        pass

    # --- fitness --------------------------------------------------------------
    fit = _rows(conn, """
        SELECT date, vo2max, fitness_age, race_5k_s, race_10k_s, race_half_s, endurance_score
        FROM fitness WHERE vo2max IS NOT NULL ORDER BY date DESC LIMIT 1
    """)
    if fit:
        f = fit[0]
        bits = [f"VO2max {_fmt(f['vo2max'], nd=1)}"]
        if f["fitness_age"]:
            bits.append(f"fitness age {f['fitness_age']}")
        if f["endurance_score"]:
            bits.append(f"endurance {f['endurance_score']}")
        preds = [f"5k {_pace(f['race_5k_s'] and f['race_5k_s'] / 5)}" if f["race_5k_s"] else "",
                 f"half {_pace(f['race_half_s'] and f['race_half_s'] / 21.0975)}" if f["race_half_s"] else ""]
        preds = [p for p in preds if p]
        out.append(f"Fitness ({f['date']}): " + " · ".join(bits)
                   + (f" · Garmin race pace predictions: {', '.join(preds)}" if preds else ""))

    # --- recent activities ----------------------------------------------------
    acts = _rows(conn, """
        SELECT date, type, name, distance_m, duration_s, avg_pace_s_per_km,
               avg_hr, max_hr, training_load, decoupling_pct
        FROM activities WHERE date >= date('now','-21 days')
        ORDER BY date DESC, start_time_local DESC LIMIT 40
    """)
    out.append("\n## Activities, last 21 days")
    if acts:
        out.append("date | type | dist | dur | pace | avg HR | load")
        for a in acts:
            km = f"{a['distance_m'] / 1000:.2f} km" if a["distance_m"] else "--"
            mins = f"{a['duration_s'] / 60:.0f} min" if a["duration_s"] else "--"
            out.append(
                f"{a['date']} | {a['type']} | {km} | {mins} | "
                f"{_pace(a['avg_pace_s_per_km'])} | {_fmt(a['avg_hr'])} | "
                f"{_fmt(a['training_load'], nd=1)}"
            )
    else:
        out.append("(no activities logged in this window)")

    # --- recovery -------------------------------------------------------------
    rec = _rows(conn, """
        SELECT d.date,
               s.total_sleep_s/3600.0 AS sleep_h, s.sleep_score,
               h.last_night_avg AS hrv, h.status AS hrv_status,
               r.resting_hr, st.avg_stress, bb.drained, bb.charged
        FROM (SELECT date FROM sleep UNION SELECT date FROM hrv
              UNION SELECT date FROM rhr UNION SELECT date FROM stress) d
        LEFT JOIN sleep s ON s.date = d.date
        LEFT JOIN hrv h ON h.date = d.date
        LEFT JOIN rhr r ON r.date = d.date
        LEFT JOIN stress st ON st.date = d.date
        LEFT JOIN body_battery bb ON bb.date = d.date
        WHERE d.date >= date('now','-14 days')
        ORDER BY d.date DESC
    """)
    out.append("\n## Recovery, last 14 days  (-- = not recorded, NOT zero)")
    if rec:
        out.append("date | sleep h | score | HRV | HRV status | rest HR | stress | BB drain")
        for r in rec:
            out.append(
                f"{r['date']} | {_fmt(r['sleep_h'], nd=1)} | {_fmt(r['sleep_score'])} | "
                f"{_fmt(r['hrv'])} | {r['hrv_status'] or '--'} | {_fmt(r['resting_hr'])} | "
                f"{_fmt(r['avg_stress'])} | {_fmt(r['drained'])}"
            )
    else:
        out.append("(no recovery data in this window)")

    # HRV baseline gives "suppressed vs normal" meaning to the raw numbers above.
    base = _rows(conn, "SELECT baseline_low, baseline_high, weekly_avg FROM hrv "
                       "WHERE baseline_low IS NOT NULL ORDER BY date DESC LIMIT 1")
    if base:
        b = base[0]
        out.append(f"HRV baseline range {_fmt(b['baseline_low'])}-{_fmt(b['baseline_high'])} ms"
                   f" · weekly avg {_fmt(b['weekly_avg'])} ms")

    # --- upcoming -------------------------------------------------------------
    up = _rows(conn, """
        SELECT date, title, sport FROM planned_workouts
        WHERE date >= date('now') ORDER BY date LIMIT 10
    """)
    out.append("\n## Planned workouts ahead")
    if up:
        for w in up:
            out.append(f"{w['date']} | {w['title']} ({w['sport']})")
        out.append("(Garmin replaces past prescriptions with the logged activity, "
                   "so this is forward-looking only.)")
    else:
        out.append("(nothing scheduled)")

    # Personal records are deliberately NOT inlined: Garmin stores `value` as a
    # bare number whose unit varies by record type (metres for distances,
    # seconds for times, watts for power) and some rows are junk ("Type 17").
    # Quoting them unlabelled invites a confidently wrong number, so leave them
    # behind the tools where the coach must look at the type before using one.
    if _scalar(conn, "SELECT COUNT(*) FROM personal_records", 0):
        out.append("\nPersonal records exist but are stored with per-type units; "
                   "query `personal_records` via run_sql before quoting any of them.")

    # --- coverage -------------------------------------------------------------
    first = _scalar(conn, "SELECT MIN(date) FROM activities")
    last = _scalar(conn, "SELECT MAX(date) FROM activities")
    n_act = _scalar(conn, "SELECT COUNT(*) FROM activities", 0)
    out.append(f"\nData coverage: {n_act} activities, {first} to {last}. "
               f"DB: {config.DB_PATH.name}")
    return "\n".join(out)


def build_prompt(question: str, briefing: Optional[str] = None,
                 include_briefing: bool = True) -> str:
    """Assemble the user turn: briefing first, then the question.

    On follow-up turns the briefing is omitted -- the resumed session already
    has it, and resending would waste the prompt cache.
    """
    if not include_briefing:
        return question
    briefing = briefing if briefing is not None else build_briefing()
    return f"{briefing}\n\n---\n\n{question}"
