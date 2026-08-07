"""Generate a realistic *fake* dataset so you can explore the dashboard and the
MCP coach before connecting a real Garmin account.

    python scripts/seed_sample_data.py --days 120

Writes into a SEPARATE db (data/garmin_sample.db by default) so it never touches
your real history. Point the dashboard/MCP at it with GARMIN_DB=... if you like.
Deterministic (fixed seed) so results are reproducible.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from garmin_coach import db  # noqa: E402

RUN_TYPES = ["running", "running", "running", "trail_running"]


def build(days: int, db_path: Path):
    rng = random.Random(42)
    db.init_db(db_path)
    conn = db.connect(db_path)
    today = dt.date.today()
    fitness = 0.0  # slow drift → easy pace at fixed HR improves over time

    for i in range(days):
        d = (today - dt.timedelta(days=days - 1 - i))
        ds = d.isoformat()
        fitness += rng.uniform(0.0, 0.06)  # gradual improvement
        stress_base = rng.randint(25, 45)
        hrv = int(rng.gauss(65 + fitness, 6))
        sleep_h = max(4.5, min(9.0, rng.gauss(7.2, 1.0)))
        rhr_v = int(rng.gauss(50 - fitness * 0.3, 2))

        db.upsert(conn, "sleep", {
            "date": ds, "total_sleep_s": int(sleep_h * 3600),
            "deep_s": int(sleep_h * 3600 * 0.18), "light_s": int(sleep_h * 3600 * 0.55),
            "rem_s": int(sleep_h * 3600 * 0.22), "awake_s": int(sleep_h * 3600 * 0.05),
            "sleep_score": int(min(95, max(40, rng.gauss(78, 10)))),
            "avg_hr": rhr_v + 2,
        }, pk=["date"])
        db.upsert(conn, "hrv", {
            "date": ds, "last_night_avg": hrv, "weekly_avg": hrv,
            "status": "BALANCED" if hrv > 55 else "UNBALANCED",
            "baseline_low": 50, "baseline_high": 80,
        }, pk=["date"])
        db.upsert(conn, "rhr", {"date": ds, "resting_hr": rhr_v,
                                "min_hr": rhr_v - 4, "max_hr": 150}, pk=["date"])
        db.upsert(conn, "stress", {
            "date": ds, "avg_stress": stress_base, "max_stress": stress_base + rng.randint(30, 55),
            "rest_min": 600, "low_min": 500, "med_min": 200, "high_min": rng.randint(10, 60),
        }, pk=["date"])
        db.upsert(conn, "body_battery", {
            "date": ds, "charged": rng.randint(40, 80), "drained": rng.randint(40, 80),
            "high": rng.randint(75, 100), "low": rng.randint(5, 30),
        }, pk=["date"])
        db.upsert(conn, "readiness", {
            "date": ds, "score": int(min(100, max(20, rng.gauss(70, 15)))),
            "level": "READY", "recovery_time_h": rng.randint(6, 36),
        }, pk=["date"])
        db.upsert(conn, "training_status", {
            "date": ds, "status": rng.choice(["PRODUCTIVE", "MAINTAINING", "PRODUCTIVE"]),
            "vo2max": round(52 + fitness * 0.4, 1), "load_ratio": round(rng.uniform(0.8, 1.3), 2),
        }, pk=["date"])
        db.upsert(conn, "daily_stats", {
            "date": ds, "steps": rng.randint(6000, 15000), "calories": rng.randint(2200, 3200),
            "intensity_min": rng.randint(20, 90),
        }, pk=["date"])

        # ~5 runs/week
        if rng.random() < 0.72:
            _make_run(conn, rng, d, fitness)
        conn.commit()

    conn.close()


def _make_run(conn, rng, d, fitness):
    aid = int(d.strftime("%Y%m%d")) * 10 + rng.randint(0, 9)
    hard = rng.random() < 0.25
    dist_km = rng.choice([6, 8, 8, 10, 12, 16, 21]) if not hard else rng.choice([8, 10, 12])
    # easy pace ~5:40/km improving with fitness; hard ~4:15/km
    base_pace = (340 if not hard else 255) - fitness * 1.2
    pace = base_pace + rng.uniform(-8, 8)
    avg_hr = int((138 if not hard else 168) + rng.uniform(-5, 5))
    dur = pace * dist_km
    atype = rng.choice(RUN_TYPES)
    load = round(dist_km * (1.4 if hard else 1.0) * rng.uniform(0.9, 1.1), 1)
    # time-in-zone roughly reflecting easy vs hard
    if hard:
        z = [0, int(dur * 0.2), int(dur * 0.25), int(dur * 0.4), int(dur * 0.15)]
    else:
        z = [int(dur * 0.15), int(dur * 0.7), int(dur * 0.15), 0, 0]
    db.upsert(conn, "activities", {
        "activity_id": aid, "start_time_local": f"{d.isoformat()} 07:30:00", "date": d.isoformat(),
        "type": atype, "name": ("Tempo" if hard else "Easy") + " run",
        "distance_m": dist_km * 1000, "duration_s": dur, "moving_s": dur,
        "avg_pace_s_per_km": round(pace, 1), "avg_hr": avg_hr, "max_hr": avg_hr + rng.randint(8, 20),
        "avg_cadence": rng.randint(168, 182), "elev_gain_m": rng.randint(20, 200),
        "training_load": load, "training_effect_aerobic": round(rng.uniform(2.0, 4.5), 1),
        "decoupling_pct": round(rng.uniform(1.5, 8.0) - fitness * 0.05, 1),
        "z1_s": z[0], "z2_s": z[1], "z3_s": z[2], "z4_s": z[3], "z5_s": z[4],
    }, pk=["activity_id"])
    # a few splits
    for k in range(int(dist_km)):
        db.upsert(conn, "activity_splits", {
            "activity_id": aid, "split_idx": k, "distance_m": 1000,
            "duration_s": round(pace + rng.uniform(-6, 6), 1),
            "avg_pace_s_per_km": round(pace + rng.uniform(-6, 6), 1),
            "avg_hr": avg_hr + rng.randint(-4, 6),
        }, pk=["activity_id", "split_idx"])
    # simple synthetic per-second stream (for the drill-down + decoupling)
    n = int(dur)
    for s in range(0, n, 5):  # 5s resolution to keep it light
        drift = 1 + (s / n) * (0.06 if not hard else 0.03)  # cardiac drift
        db.upsert(conn, "activity_streams", {
            "activity_id": aid, "offset_s": s,
            "hr": int(avg_hr * drift + rng.uniform(-3, 3)),
            "speed_mps": round(1000.0 / pace + rng.uniform(-0.1, 0.1), 3),
            "cadence": rng.randint(168, 182),
            "altitude_m": 50 + 20 * math.sin(s / 200.0),
        }, pk=["activity_id", "offset_s"])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--db", default=str(config.DATA_DIR / "garmin_sample.db"))
    args = p.parse_args()
    path = Path(args.db)
    build(args.days, path)
    print(f"Seeded {args.days} days of sample data into {path}")
    print(f"Explore it with:  GARMIN_DB={path} streamlit run garmin_coach/dashboard/app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
