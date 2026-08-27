# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Garmin Coach: pulls all Garmin Connect data into local SQLite, serves a standalone Streamlit dashboard, and exposes the data to Claude as a read-only MCP "running coach". See `README.md` for the user-facing setup.

## Commands

All commands run from the project root using the venv's Python. On Windows the venv is `.venv\Scripts\python.exe`.

```bash
# Setup
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e .

# One-time interactive Garmin login (password + MFA) -> writes token cache to ~/.garminconnect/
python scripts/bootstrap_login.py

# Ingest (three modes)
python -m garmin_coach.ingest.run --nightly              # trailing days + recent activities
python -m garmin_coach.ingest.run --date 2026-07-15      # single day
python -m garmin_coach.ingest.run --backfill 46 --no-streams  # last N days; skip per-second streams

# Dashboard (reads data/garmin.db)
streamlit run garmin_coach/dashboard/app.py

# MCP server (launch by FILE PATH, not `-m`, so it works from any CWD)
python garmin_coach/mcp/server.py
```

### Testing / verifying without a live Garmin account

```bash
python scripts/seed_sample_data.py --days 150     # writes data/garmin_sample.db (fake, deterministic)
GARMIN_DB=data/garmin_sample.db streamlit run garmin_coach/dashboard/app.py

python scripts/test_mcp.py                          # end-to-end MCP: lists tools, calls each, checks write-guard

# AI coach: CLI present -> briefing builds -> a real turn reaches the MCP tools.
# Catches the silent "MCP never connected, answered from briefing only" failure.
PYTHONUTF8=1 python scripts/test_coach.py
PYTHONUTF8=1 python scripts/test_coach.py --offline  # skip the live Claude turn

# Dashboard smoke test (runs the whole app headless, surfaces any exception) — the primary "test":
PYTHONUTF8=1 python -c "from streamlit.testing.v1 import AppTest; \
at=AppTest.from_file('garmin_coach/dashboard/app.py',default_timeout=90).run(); \
print('CLEAN' if not at.exception else at.exception)"
```

There is no unit-test suite; `AppTest` + `scripts/test_mcp.py` + `scripts/test_coach.py` against the sample DB are the verification path.

## Environment gotchas (Windows)

- **`GARMIN_DB` env var overrides the DB path.** The sample scripts set it to `garmin_sample.db`; in a bash session, `unset GARMIN_DB` before targeting the real `data/garmin.db`.
- **Restarting the dashboard:** `pkill -f "streamlit run"` does NOT match on Windows (the process is `python.exe -m streamlit`). Kill by port instead, or stale servers accumulate and keep serving old code:
  ```powershell
  Get-NetTCPConnection -LocalPort 8501 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```
- **Scripts printing emoji** need `PYTHONUTF8=1` (Windows cp1252 stdout otherwise raises UnicodeEncodeError).
- **Never spawn `claude` through npm's `claude.cmd`.** The shim is a batch file, so Windows re-parses the command line through cmd.exe, and an argument containing a **newline** — like the coach's multi-line `--system-prompt` — terminates that line. Every flag after it is silently dropped: `--mcp-config` and `--strict-mcp-config` vanish, the Garmin MCP server never loads, and the user's unrelated *global* MCP servers get pulled in instead. No error is raised — the coach just answers with no data and claims the tools aren't connected. `claude_cli.find_cli()` resolves through the shim to the real `node_modules/@anthropic-ai/claude-code/bin/claude.exe`; keep that behaviour. Verify with the `system`/`init` event, which lists `mcp_servers` — `garmin` must be `connected` (surfaced as `Reply.mcp_connected`).

## Architecture

Three decoupled layers (pull → store → read) so a Garmin outage or auth break never corrupts local data. The Garmin client is **unofficial** (`python-garminconnect`, mimics the Android app) and fragile — the design mitigates this with pull-once-cache-forever and defensive parsing.

**Pull** — `garmin_coach/ingest/`
- `client.py` wraps the Garmin client with a throttle + bounded `tenacity` retry. Auth failures are fatal (never headless-retry credentials — issue #312).
- `wellness.py` pulls per-day daily metrics; `activities.py` pulls per-date-range activity summaries + splits + HR zones + (opt-in, runs only) per-second streams.
- `transforms.py` normalizes Garmin's varied/versioned JSON into flat rows — **every extractor is defensive** (probes multiple key paths, returns None not raises). Activity `raw_json` is stored so transforms can be re-run without re-hitting Garmin.
- `run.py` is the CLI orchestrator. Backfill is resumable via the `ingest_log` table (per-date/dataset ok/missing/error). Idempotent: every write is UPSERT on a natural key.
- `launcher.py` backs the dashboard's **Pull my latest data** button (Overview tab). It *shells out to the same `python -m garmin_coach.ingest.run` CLI* rather than importing it — the dashboard's connections are read-only and ingestion is the sole writer, an unofficial-client hang can be killed without taking Streamlit down, and the button, the terminal and Task Scheduler all share one code path. Streams log lines to `st.status`, holds a stale-tolerant `data/ingest.lock` so two browser tabs can't double-pull, and maps exit 3 → "tokens expired, re-run `bootstrap_login.py`". **After a successful pull it must call `st.cache_data.clear()`** — `q()` is cached with a 5-minute TTL and would otherwise keep serving pre-pull rows.

**Store** — `garmin_coach/db.py` + `schema.sql`
- `connect()` r/w for ingestion; `connect_ro()` read-only (URI immutable) for dashboard + MCP.
- Schema evolves two ways: new tables via `CREATE TABLE IF NOT EXISTS` in `schema.sql`; new columns via the `_MIGRATIONS` list in `db.py` (idempotent `ALTER TABLE ADD COLUMN`). `init_db()` applies both.

**Read** — three consumers of the same store:
- `dashboard/app.py` (Streamlit + Plotly). Theming lives in `.streamlit/config.toml` + an injected CSS block; charts share the `_style()` helper and a validated colorblind-safe palette. Never use dual-axis charts (split into stacked single-axis).
- `mcp/server.py` (FastMCP, stdio). **Read-only**: DB opened `mode=ro`, and `run_sql` rejects anything that isn't a single SELECT/WITH. Tools auto-format pace to `min_km` alongside raw seconds.
- `ai/` — the dashboard's **AI coach** tab. Shells out to the local **Claude Code CLI** (`claude -p`), *not* the Anthropic API, so coaching rides the user's existing subscription and no API key ever lives in this project. `ai/claude_cli.py` is the transport (spawn, stream, parse); `ai/coach.py` is the domain layer (system prompt, `build_briefing()`, suggested questions). It launches the MCP server above via `--mcp-config`, allow-listing only `mcp__garmin__*` — no Bash/Edit/Write, and in `-p` mode a non-allow-listed tool is denied rather than prompted, so an unattended run can never block or mutate.

**Why a briefing *and* tools:** `build_briefing()` pre-computes a ~800-token snapshot (plan, zones, ACWR, 21d activities, 14d recovery, upcoming sessions) so the common questions need zero tool calls and the coach always answers from the same slice the charts show. Tools cover the depth beyond it. Personal records are deliberately left out of the briefing — `personal_records.value` is a bare number whose unit varies by type, so quoting it unlabelled invites a confidently wrong answer.

**Shared analytics** — `garmin_coach/analytics/metrics.py` backs both the dashboard and MCP so fitness is computed identically everywhere: decoupling, ACWR, pace-at-fixed-HR, zone distribution, and `effective_zones()`.

## Key domain facts

- **HR zones are dynamic, not hardcoded.** `effective_zones()` reads the latest row of the `hr_zones` table (boundaries Garmin applied to each activity) and derives easy ceiling / LTHR / max HR. `.env` HR_* are commented out — used only as manual overrides. Don't reintroduce hardcoded zones.
- **`garminconnect` 0.3.6 auth:** `client.login(tokenstore)` both logs in AND persists tokens (no `.garth.dump()`). `auth.login()` loads the cache only and raises `TokenExpiredError` (→ exit code 3) rather than looping.
- **"missing" ≠ "error".** Many endpoints are genuinely empty for a given account/device (e.g. Training Readiness, Training Status, SpO2 here). Transforms return None → logged `missing`; charts must not plot these as zero.
- **Data volume:** this user's Garmin history only starts ~2026-06-09; older dates return empty. A full-year backfill is wasteful.
- The user is on a Garmin adaptive **Half Marathon** plan (`training_plan`/`planned_workouts` tables) — planned-vs-actual is forward-looking only (Garmin replaces past prescriptions with the logged activity).

## Product direction

Parked notes on turning this into a product (the data-door problem, platform trade-offs, competitive landscape) live in `NOTES-productization.md` — **untracked and gitignored**, since this repo is public. Read it before reopening that question; the short version is that multi-user is gated on replacing `python-garminconnect`, which cannot custody other people's Garmin credentials.

