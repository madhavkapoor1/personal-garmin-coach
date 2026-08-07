-- Garmin Coach local store. One row per day per metric family (denormalized)
-- so ad-hoc SQL (and Claude) stays trivial. All dates are 'YYYY-MM-DD' text.
-- Every table is safe to UPSERT into: re-pulling a day corrects it in place.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------- daily wellness (PK = date) --------------------------------------
CREATE TABLE IF NOT EXISTS sleep (
    date            TEXT PRIMARY KEY,
    total_sleep_s   INTEGER,
    deep_s          INTEGER,
    light_s         INTEGER,
    rem_s           INTEGER,
    awake_s         INTEGER,
    sleep_score     INTEGER,
    avg_spo2        REAL,
    avg_resp        REAL,
    avg_hr          INTEGER,
    restless_count  INTEGER,
    bedtime_local   TEXT,
    wake_local      TEXT
);

CREATE TABLE IF NOT EXISTS hrv (
    date              TEXT PRIMARY KEY,
    last_night_avg    INTEGER,
    last_night_5m_high INTEGER,
    weekly_avg        INTEGER,
    status            TEXT,        -- BALANCED / UNBALANCED / LOW / POOR
    baseline_low      INTEGER,
    baseline_high     INTEGER
);

CREATE TABLE IF NOT EXISTS stress (
    date         TEXT PRIMARY KEY,
    avg_stress   INTEGER,
    max_stress   INTEGER,
    rest_min     INTEGER,
    low_min      INTEGER,
    med_min      INTEGER,
    high_min     INTEGER
);

CREATE TABLE IF NOT EXISTS body_battery (
    date       TEXT PRIMARY KEY,
    charged    INTEGER,
    drained    INTEGER,
    high       INTEGER,
    low        INTEGER,
    start_val  INTEGER,
    end_val    INTEGER
);

CREATE TABLE IF NOT EXISTS rhr (
    date        TEXT PRIMARY KEY,
    resting_hr  INTEGER,
    min_hr      INTEGER,
    max_hr      INTEGER,
    avg_hr      INTEGER
);

CREATE TABLE IF NOT EXISTS readiness (
    date            TEXT PRIMARY KEY,
    score           INTEGER,
    level           TEXT,
    feedback        TEXT,
    sleep_score     INTEGER,
    recovery_time_h INTEGER,
    hrv_factor      INTEGER,
    acute_load      INTEGER,
    stress_factor   INTEGER
);

CREATE TABLE IF NOT EXISTS training_status (
    date          TEXT PRIMARY KEY,
    status        TEXT,      -- PRODUCTIVE / MAINTAINING / OVERREACHING / ...
    vo2max        REAL,
    acute_load    INTEGER,
    load_ratio    REAL,      -- Garmin's own acute:chronic-ish ratio
    fitness_trend TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date          TEXT PRIMARY KEY,
    steps         INTEGER,
    calories      INTEGER,
    distance_m    REAL,
    intensity_min INTEGER,
    floors        INTEGER,
    resting_hr    INTEGER,
    bb_max        INTEGER
);

-- ---------- activities ------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
    activity_id             INTEGER PRIMARY KEY,
    start_time_local        TEXT,
    date                    TEXT,     -- for joins to daily wellness
    type                    TEXT,     -- running / trail_running / cycling / ...
    name                    TEXT,
    distance_m              REAL,
    duration_s              REAL,
    moving_s                REAL,
    avg_pace_s_per_km       REAL,
    avg_hr                  INTEGER,
    max_hr                  INTEGER,
    avg_cadence             REAL,
    avg_power               REAL,
    elev_gain_m             REAL,
    avg_temp                REAL,
    calories                INTEGER,
    training_effect_aerobic REAL,
    training_effect_anaerobic REAL,
    training_load           REAL,     -- per-activity load (feeds ACWR)
    z1_s INTEGER, z2_s INTEGER, z3_s INTEGER, z4_s INTEGER, z5_s INTEGER,
    decoupling_pct          REAL,     -- computed at ingest (analytics.metrics)
    raw_json                TEXT
);

CREATE TABLE IF NOT EXISTS activity_splits (
    activity_id       INTEGER,
    split_idx         INTEGER,
    distance_m        REAL,
    duration_s        REAL,
    avg_pace_s_per_km REAL,
    avg_hr            INTEGER,
    avg_cadence       REAL,
    elev_gain_m       REAL,
    PRIMARY KEY (activity_id, split_idx)
);

-- per-second stream, opt-in for runs only (storage-heavy)
CREATE TABLE IF NOT EXISTS activity_streams (
    activity_id INTEGER,
    offset_s    INTEGER,
    hr          INTEGER,
    speed_mps   REAL,
    cadence     REAL,
    altitude_m  REAL,
    power       REAL,
    lat         REAL,
    lon         REAL,
    PRIMARY KEY (activity_id, offset_s)
);

-- ---------- ops -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_log (
    date       TEXT,
    dataset    TEXT,
    status     TEXT,     -- ok / missing / error
    msg        TEXT,
    updated_at TEXT,
    PRIMARY KEY (date, dataset)
);

-- ---------- fitness snapshot (PK = date) ------------------------------------
CREATE TABLE IF NOT EXISTS fitness (
    date            TEXT PRIMARY KEY,
    vo2max          REAL,
    vo2max_precise  REAL,
    fitness_age     REAL,
    race_5k_s       INTEGER,   -- predicted finish times (seconds)
    race_10k_s      INTEGER,
    race_half_s     INTEGER,
    race_marathon_s INTEGER,
    endurance_score INTEGER,
    hill_score      INTEGER
);

-- ---------- strength training sets ------------------------------------------
CREATE TABLE IF NOT EXISTS strength_sets (
    activity_id       INTEGER,
    set_idx           INTEGER,
    set_type          TEXT,     -- ACTIVE / REST
    exercise_category TEXT,     -- BENCH_PRESS / SQUAT / ...
    exercise_name     TEXT,     -- DUMBBELL_BENCH_PRESS / ...
    reps              INTEGER,
    weight_kg         REAL,
    duration_s        REAL,
    PRIMARY KEY (activity_id, set_idx)
);

-- ---------- HR zone boundaries (dynamic, captured per activity) -------------
-- Garmin recalculates zones as fitness changes; each activity records the
-- boundaries in force at that time. The most recent row = current zones.
CREATE TABLE IF NOT EXISTS hr_zones (
    date        TEXT PRIMARY KEY,
    activity_id INTEGER,
    z1_low INTEGER, z2_low INTEGER, z3_low INTEGER, z4_low INTEGER, z5_low INTEGER
);

-- ---------- personal records ------------------------------------------------
CREATE TABLE IF NOT EXISTS personal_records (
    pr_id         INTEGER PRIMARY KEY,
    type_id       INTEGER,
    label         TEXT,
    value         REAL,
    activity_id   INTEGER,
    activity_name TEXT,
    date          TEXT
);

-- ---------- training plan & planned workouts --------------------------------
CREATE TABLE IF NOT EXISTS training_plan (
    plan_id    INTEGER PRIMARY KEY,
    name       TEXT,
    category   TEXT,
    sport      TEXT,
    level      TEXT,
    start_date TEXT,
    end_date   TEXT
);

CREATE TABLE IF NOT EXISTS workouts (             -- reusable templates
    workout_id     INTEGER PRIMARY KEY,
    name           TEXT,
    sport          TEXT,
    description    TEXT,
    est_duration_s INTEGER
);

CREATE TABLE IF NOT EXISTS planned_workouts (     -- calendar: what the plan prescribes
    id              INTEGER PRIMARY KEY,
    date            TEXT,
    title           TEXT,
    sport           TEXT,
    item_type       TEXT,
    training_plan_id INTEGER
);

-- ---------- intraday all-day HR (downsampled ~5 min) ------------------------
CREATE TABLE IF NOT EXISTS intraday_hr (
    date     TEXT,
    offset_s INTEGER,
    hr       INTEGER,
    PRIMARY KEY (date, offset_s)
);

CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(type);
CREATE INDEX IF NOT EXISTS idx_streams_act ON activity_streams(activity_id);
CREATE INDEX IF NOT EXISTS idx_strength_act ON strength_sets(activity_id);
CREATE INDEX IF NOT EXISTS idx_planned_date ON planned_workouts(date);
