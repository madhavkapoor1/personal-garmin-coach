# 🏃 Garmin Coach

A local system that pulls **all** your Garmin Connect data (runs, sleep, HRV, stress, Body Battery, resting HR, training readiness/status) into a SQLite store, shows it in a **standalone Streamlit dashboard** (useful with zero AI), and exposes it to **Claude as an AI running coach** via a local MCP server.

Inspired by the *Running on AI* field guide — this implements **Level 4** (own the pipe: nightly pull → one store → dashboard) and **Level 5** (Claude reads the store and coaches). The Claude link is a **local MCP server**, so coaching is free within your Claude subscription — no API key, no per-token cost.

```
Garmin Connect ──(ingest)──▶ SQLite (data/garmin.db) ──▶ Dashboard (Streamlit)
                                   │
                                   └──▶ MCP server (read-only) ──▶ Claude Code / Desktop
```

## Layout

| Path | What |
|---|---|
| `config.py` | Paths, HR markers, throttle/backfill settings (reads `.env`). |
| `garmin_coach/auth.py` | Token-cache login; never headless-retries credentials. |
| `garmin_coach/schema.sql` · `db.py` | SQLite schema + UPSERT/connection helpers. |
| `garmin_coach/ingest/` | `client` (throttle+retry), `wellness`, `activities`, `transforms`, `run` (CLI). |
| `garmin_coach/analytics/metrics.py` | Decoupling, ACWR, pace-at-HR, zone split — shared everywhere. |
| `garmin_coach/dashboard/app.py` | Streamlit dashboard (6 tabs). |
| `garmin_coach/mcp/server.py` | Read-only MCP server for Claude. |
| `scripts/bootstrap_login.py` | One-time interactive (MFA) login. |
| `scripts/nightly.ps1` | Task Scheduler wrapper. |
| `scripts/seed_sample_data.py` | Generate fake data to explore without a Garmin account. |

## Setup

```powershell
# 1. Create venv + install
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

# 2. Configure
copy .env.example .env      # then edit: GARMIN_EMAIL + your HR markers
```

### Try it immediately with sample data (no Garmin needed)

```powershell
python scripts\seed_sample_data.py --days 150
$env:GARMIN_DB = "data\garmin_sample.db"
streamlit run garmin_coach\dashboard\app.py
```

### Connect your real Garmin

```powershell
python scripts\bootstrap_login.py        # enter password + MFA once
python -m garmin_coach.ingest.run --backfill 365    # pull a year (throttled)
Remove-Item Env:\GARMIN_DB               # (if set) point back at the real DB
streamlit run garmin_coach\dashboard\app.py
```

Ingest modes:

```powershell
python -m garmin_coach.ingest.run --nightly           # trailing days + recent activities
python -m garmin_coach.ingest.run --date 2026-07-13    # one day
python -m garmin_coach.ingest.run --backfill 90 --no-streams   # skip per-second streams
```

## Connect Claude (the coach)

Register the MCP server with Claude Code (run from the project root):

```powershell
claude mcp add garmin-coach -- "$PWD\.venv\Scripts\python.exe" -m garmin_coach.mcp.server
```

Or add to **Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "garmin-coach": {
      "command": "C:\\Users\\<you>\\Desktop\\My Stuff\\garmin-project\\.venv\\Scripts\\python.exe",
      "args": ["-m", "garmin_coach.mcp.server"],
      "env": { "GARMIN_DB": "C:\\Users\\<you>\\Desktop\\My Stuff\\garmin-project\\data\\garmin.db" }
    }
  }
}
```

Then ask Claude things like:

- *"Pull my last 14 days and tell me how my aerobic fitness is trending."*
- *"Is my easy running actually easy? Show the zone split."*
- *"Run my weekly review."* (uses the built-in `weekly_review` prompt)

The server is **read-only** — `run_sql` rejects anything that isn't a `SELECT`, so Claude can never modify your data. Tools: `get_schema`, `run_sql`, `get_recent_metrics`, `list_activities`, `get_activity_detail`, `get_training_load`, `get_pace_at_hr`.

## Automate the nightly pull

```powershell
$act = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\nightly.ps1`""
$trg = New-ScheduledTaskTrigger -Daily -At 6:30am
Register-ScheduledTask -TaskName "GarminCoachNightly" -Action $act -Trigger $trg `
    -Description "Pull Garmin data into the local store"
```

Logs land in `data\logs\`. If your Garmin tokens expire (~yearly, or on password change), the job exits 3 and shows a toast — just re-run `bootstrap_login.py`.

## Notes & caveats

- **Unofficial Garmin client.** `garminconnect` mimics the Garmin app; it's for personal use and can break if Garmin changes auth. It's pinned; `pip install -U garminconnect` if a pull suddenly fails. Data is pulled once and cached, so outages never corrupt history.
- **Credentials.** Your password is only entered during `bootstrap_login.py` and never written to `.env`. Tokens live in `~/.garminconnect/`. Optionally `--use-keyring` stores the password in Windows Credential Manager.
- **Missing ≠ zero.** Sleep/HRV arrive hours after you wake; the nightly job re-pulls a trailing window and records `missing` vs `error` in `ingest_log`, so charts never plot fake zeros.
- **Optional Level-5 agent.** A scheduled Claude-API "morning briefing" can be added later (`pip install -e ".[agent]"`); it reuses `analytics/metrics.py`. Not built yet.

## Verify

```powershell
python scripts\seed_sample_data.py --days 120     # seed
python scripts\test_mcp.py                          # exercise every MCP tool
$env:GARMIN_DB="data\garmin_sample.db"; streamlit run garmin_coach\dashboard\app.py
```
