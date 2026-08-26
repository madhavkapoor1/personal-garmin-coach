"""Garmin Coach dashboard. Standalone: reads the SQLite store, no AI required.

Run:  streamlit run garmin_coach/dashboard/app.py
Or against the sample DB:  GARMIN_DB=data/garmin_sample.db streamlit run ...
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Make project root importable when launched via `streamlit run`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from garmin_coach import db  # noqa: E402
from garmin_coach.ai import claude_cli, coach  # noqa: E402
from garmin_coach.analytics import metrics  # noqa: E402
from garmin_coach.ingest import launcher  # noqa: E402

# =============================================================================
# DESIGN SYSTEM — "Performance Telemetry"
# Dark carbon instrument panel · one volt-lime signature accent used with
# discipline (brand, active nav, and "you / now / target" markers only).
# Chart data colours are the dataviz skill's validated DARK, colourblind-safe
# palette (validated on this surface; worst adjacent CVD ΔE 8.4, normal 19.3).
# =============================================================================

# categorical data palette (dark-surface validated, fixed order — never cycled)
PALETTE = ["#3987e5", "#008300", "#d55181", "#c98500",
           "#199e70", "#d95926", "#9085e9", "#e66767"]
# Z1..Z5 heart-rate zones: the cool→hot domain ramp (always directly labelled)
ZONE_COLORS = ["#3987e5", "#199e70", "#c98500", "#d95926", "#e66767"]
# HR-as-temperature continuous scale (low=cool → high=hot), reused everywhere
HR_SCALE = ["#3987e5", "#199e70", "#c98500", "#d95926", "#e66767"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

# signature accent (chrome only — brand / active nav / you-now-target markers)
ACCENT, ACCENT_DIM = "#c8f13a", "#7f9b1f"

# chart chrome / ink tokens (dark surface)
PLANE, SURFACE, SURFACE2 = "#0c0c0b", "#161613", "#1e1e1a"
INK, INK2, MUTED = "#f4f3ee", "#c3c2b7", "#8a8880"
GRID, BASELINE = "#26261f", "#3a3a32"

px.defaults.color_discrete_sequence = PALETTE
px.defaults.template = "plotly_dark"

CHART_CONFIG = {"displayModeBar": False, "responsive": True}

st.set_page_config(page_title="Garmin Coach", page_icon="🏃", layout="wide")

# --- design system: fonts, atmosphere, custom components ----------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

:root {
  --accent:#c8f13a; --accent-dim:#7f9b1f;
  --plane:#0c0c0b; --surface:#161613; --surface2:#1e1e1a;
  --ink:#f4f3ee; --ink2:#c3c2b7; --muted:#8a8880;
  --line:rgba(244,243,238,.09); --line2:rgba(244,243,238,.05);
  --disp:'Chakra Petch', system-ui, sans-serif;
  --body:'Archivo', system-ui, sans-serif;
  --mono:'JetBrains Mono', ui-monospace, monospace;
}

/* --- atmosphere: carbon plane + a single volt glow + fine grain --- */
.stApp {
  background:
    radial-gradient(1100px 520px at 88% -8%, rgba(200,241,58,.10), transparent 60%),
    radial-gradient(900px 600px at 8% 4%, rgba(57,135,229,.07), transparent 55%),
    var(--plane);
}
.stApp::before {
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0; opacity:.5;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");
}
.block-container { padding-top:1.4rem; padding-bottom:4rem; max-width:1340px; position:relative; z-index:1; }

/* hide default Streamlit chrome for a product feel */
#MainMenu, header[data-testid="stHeader"], footer, div[data-testid="stToolbar"] { display:none !important; }
[data-testid="stDecoration"] { display:none !important; }

/* --- typography --- */
html, body, [class*="css"], .stMarkdown, p, span, div { font-family:var(--body); }
h1,h2,h3,h4 { font-family:var(--disp); letter-spacing:-.01em; color:var(--ink); }
h1 { font-weight:700; font-size:2rem !important; }
h2 { font-weight:600; } h3 { font-weight:600; font-size:1.12rem !important; }

/* --- brand hero --- */
.hero { display:flex; align-items:center; justify-content:space-between; gap:20px;
        padding:6px 2px 18px; flex-wrap:wrap; }
.hero-l { display:flex; align-items:center; gap:16px; }
.mark { width:46px; height:46px; border-radius:13px; flex:0 0 auto; position:relative;
        background:linear-gradient(150deg,#1c1c18,#0e0e0c); border:1px solid var(--line);
        box-shadow:0 0 0 1px rgba(200,241,58,.12), 0 8px 24px rgba(0,0,0,.5); }
.mark::after { content:""; position:absolute; inset:0; margin:auto; width:15px; height:15px;
        background:var(--accent); border-radius:4px; transform:rotate(45deg);
        box-shadow:0 0 18px 2px rgba(200,241,58,.55); }
.hero-title { font-family:var(--disp); font-weight:700; font-size:1.62rem; line-height:1;
        letter-spacing:.01em; color:var(--ink); margin:0; }
.hero-title b { color:var(--accent); font-weight:700; }
.hero-sub { font-family:var(--mono); font-size:.72rem; color:var(--muted);
        letter-spacing:.04em; margin-top:7px; text-transform:uppercase; }
.hero-r { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.pill { font-family:var(--mono); font-size:.7rem; letter-spacing:.03em; color:var(--ink2);
        background:var(--surface); border:1px solid var(--line); border-radius:999px;
        padding:6px 12px; white-space:nowrap; }
.pill .dot { display:inline-block; width:6px; height:6px; border-radius:50%;
        background:var(--accent); margin-right:7px; vertical-align:middle;
        box-shadow:0 0 8px 1px rgba(200,241,58,.7); }

/* --- plan banner with progress-to-race --- */
.plan { background:linear-gradient(100deg, rgba(57,135,229,.08), var(--surface) 40%);
        border:1px solid var(--line); border-radius:16px; padding:15px 20px;
        margin:2px 0 22px; display:flex; align-items:center; gap:22px; flex-wrap:wrap; }
.plan-goal { font-family:var(--disp); font-weight:600; font-size:1.02rem; color:var(--ink); }
.plan-goal em { color:var(--accent); font-style:normal; }
.plan-level { font-family:var(--mono); font-size:.68rem; text-transform:uppercase;
        letter-spacing:.1em; color:var(--muted); border:1px solid var(--line);
        padding:3px 9px; border-radius:6px; margin-left:10px; }
.plan-bar { flex:1 1 220px; min-width:180px; }
.plan-track { height:7px; border-radius:999px; background:var(--surface2);
        border:1px solid var(--line2); overflow:hidden; }
.plan-fill { height:100%; border-radius:999px;
        background:linear-gradient(90deg, #6da7ec, var(--accent));
        box-shadow:0 0 12px rgba(200,241,58,.4); }
.plan-meta { font-family:var(--mono); font-size:.74rem; color:var(--ink2);
        white-space:nowrap; }
.plan-meta b { color:var(--accent); }

/* --- KPI stat tiles --- */
.kpi-row { display:grid; gap:14px; margin:6px 0 8px;
        grid-template-columns:repeat(auto-fit, minmax(168px,1fr)); }
.tile { position:relative; background:var(--surface); border:1px solid var(--line);
        border-radius:16px; padding:16px 17px 17px; overflow:hidden;
        box-shadow:0 1px 0 rgba(255,255,255,.02) inset, 0 10px 30px -18px rgba(0,0,0,.8);
        transition:border-color .18s, transform .18s; }
.tile:hover { border-color:rgba(244,243,238,.18); transform:translateY(-2px); }
.tile::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
        background:var(--bar,var(--accent-dim)); opacity:.9; }
.tile-l { font-family:var(--disp); font-weight:600; font-size:.68rem; letter-spacing:.13em;
        text-transform:uppercase; color:var(--muted); }
.tile-v { font-family:var(--disp); font-weight:700; font-size:1.95rem; line-height:1.05;
        color:var(--ink); margin-top:9px; letter-spacing:-.01em;
        font-variant-numeric:tabular-nums; }
.tile-v u { text-decoration:none; font-family:var(--mono); font-weight:500;
        font-size:.82rem; color:var(--muted); margin-left:4px; }
.tile-s { font-family:var(--mono); font-size:.73rem; margin-top:8px; color:var(--muted);
        letter-spacing:.01em; }
.tile-s.on { color:var(--tone,var(--ink2)); }

/* --- section headers (eyebrow + title) --- */
.sec { margin:26px 0 8px; }
.sec-eye { font-family:var(--mono); font-size:.68rem; letter-spacing:.16em;
        text-transform:uppercase; color:var(--accent-dim); }
.sec-t { font-family:var(--disp); font-weight:600; font-size:1.16rem; color:var(--ink);
        margin-top:3px; }
.sec-d { font-family:var(--body); font-size:.86rem; color:var(--muted); margin-top:3px;
        max-width:70ch; }

/* --- residual st.metric (kept dark & consistent) --- */
div[data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line);
        border-radius:16px; padding:15px 17px; }
div[data-testid="stMetric"] label p { color:var(--muted); font-family:var(--disp);
        font-weight:600; font-size:.68rem; letter-spacing:.12em; text-transform:uppercase; }
div[data-testid="stMetricValue"] { font-family:var(--disp); font-weight:700;
        letter-spacing:-.01em; color:var(--ink); }
div[data-testid="stMetricDelta"] { font-family:var(--mono); font-size:.78rem; }

/* --- tabs: telemetry nav --- */
div[data-baseweb="tab-list"] { gap:2px; border-bottom:1px solid var(--line);
        background:transparent; }
button[data-baseweb="tab"] { font-family:var(--disp); font-weight:600; font-size:.82rem;
        letter-spacing:.02em; color:var(--muted); padding:9px 15px; }
button[data-baseweb="tab"]:hover { color:var(--ink2); }
button[data-baseweb="tab"][aria-selected="true"] { color:var(--accent); }
div[data-baseweb="tab-highlight"] { background:var(--accent) !important; height:2px; }

/* --- inputs, dataframes, captions --- */
div[data-testid="stDataFrame"] { border-radius:12px; overflow:hidden; border:1px solid var(--line); }
div[data-testid="stCaptionContainer"], .stCaption { color:var(--muted) !important;
        font-family:var(--mono); font-size:.74rem; }
label[data-testid="stWidgetLabel"] p { color:var(--muted); font-family:var(--disp);
        font-weight:600; font-size:.7rem; letter-spacing:.1em; text-transform:uppercase; }
div[data-baseweb="select"] > div, .stNumberInput input, .stTextInput input {
        background:var(--surface) !important; border-color:var(--line) !important;
        border-radius:10px !important; }
div[data-baseweb="select"] > div:focus-within, .stNumberInput input:focus {
        border-color:var(--accent) !important; }
/* scrollbar */
::-webkit-scrollbar { width:11px; height:11px; }
::-webkit-scrollbar-thumb { background:#2c2c26; border-radius:6px;
        border:3px solid var(--plane); }
::-webkit-scrollbar-thumb:hover { background:#3a3a32; }
</style>
""", unsafe_allow_html=True)


# --- custom component helpers -------------------------------------------------
_TONES = {"default": ACCENT_DIM, "accent": ACCENT, "good": STATUS["good"],
          "warn": STATUS["warning"], "bad": STATUS["critical"], "info": PALETTE[0]}


def tile(label, value, unit="", sub="", tone="default"):
    """One KPI stat-tile (HTML string). tone drives the top accent bar + sub colour."""
    bar = _TONES.get(tone, ACCENT_DIM)
    u = f"<u>{unit}</u>" if unit else ""
    on = " on" if (sub and tone != "default") else ""
    s = f'<div class="tile-s{on}" style="--tone:{bar}">{sub}</div>' if sub else ""
    return (f'<div class="tile" style="--bar:{bar}"><div class="tile-l">{label}</div>'
            f'<div class="tile-v">{value}{u}</div>{s}</div>')


def kpi_row(cards, where=st):
    where.markdown(f'<div class="kpi-row">{"".join(cards)}</div>', unsafe_allow_html=True)


def section(title, eyebrow="", desc="", where=st):
    e = f'<div class="sec-eye">{eyebrow}</div>' if eyebrow else ""
    d = f'<div class="sec-d">{desc}</div>' if desc else ""
    where.markdown(f'<div class="sec">{e}<div class="sec-t">{title}</div>{d}</div>',
                   unsafe_allow_html=True)


@st.cache_data(ttl=300)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = db.connect_ro()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _acwr_and_zones():
    conn = db.connect_ro()
    try:
        return metrics.acwr(conn), metrics.zone_distribution(conn, 28)
    finally:
        conn.close()


@st.cache_data(ttl=300)
def get_zones():
    conn = db.connect_ro()
    try:
        return metrics.effective_zones(conn)
    finally:
        conn.close()


def fmt_pace(s: float) -> str:
    if pd.isna(s):
        return "—"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}:{sec:02d}/km"


def pace_axis(fig, series, axis="yaxis"):
    """Format a pace axis (values in seconds/km) as mm:ss ticks labelled min/km."""
    import math

    s = pd.Series(series).dropna()
    if s.empty:
        return fig
    lo, hi = float(s.min()), float(s.max())
    step = 15 if (hi - lo) <= 120 else (30 if (hi - lo) <= 300 else 60)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step + step
    vals = list(range(int(start), int(end), step))
    text = [f"{v // 60}:{v % 60:02d}" for v in vals]
    fig.update_layout(**{axis: dict(tickvals=vals, ticktext=text, title="pace (min/km)")})
    return fig


def _style(fig, height=340):
    """Apply consistent, recessive dark chart chrome: hairline grid, muted axes,
    technical mono ticks, unified hover, thin marks."""
    mono = "JetBrains Mono, ui-monospace, monospace"
    body = "Archivo, system-ui, sans-serif"
    fig.update_layout(
        height=height, margin=dict(l=10, r=16, t=46, b=10),
        font=dict(family=body, color=INK2, size=12),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0,
                    font=dict(size=11, color=INK2), title_text=""),
        hoverlabel=dict(bgcolor=SURFACE2, bordercolor=BASELINE,
                        font=dict(color=INK, size=12, family=mono)),
        hovermode="closest",
        colorway=PALETTE,
        coloraxis_colorbar=dict(outlinewidth=0, thickness=10, ticks="",
                                tickfont=dict(color=MUTED, size=10, family=mono),
                                title_font=dict(color=MUTED, size=10, family=body)),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=BASELINE,
                     ticks="outside", tickcolor=BASELINE,
                     tickfont=dict(color=MUTED, size=11, family=mono),
                     title_font=dict(color=MUTED, size=11, family=body))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)",
                     tickfont=dict(color=MUTED, size=11, family=mono),
                     title_font=dict(color=MUTED, size=11, family=body))
    # thin lines + comfortable markers as DEFAULTS — never clobber a trace that
    # set its own (e.g. accent "now" highlights, thin stream traces).
    fig.update_traces(selector=lambda t: t.type == "scatter" and t.line.width is None,
                      line=dict(width=2.2))
    fig.update_traces(selector=lambda t: t.type == "scatter" and t.marker.size is None,
                      marker=dict(size=7))
    # rounded bar ends + breathing room on any bar traces
    fig.update_traces(selector=dict(type="bar"),
                      marker_line_width=0, marker_cornerradius=5)
    fig.update_layout(bargap=0.3)
    return fig


def show(fig, height=340, where=st):
    """Style + render a Plotly chart with the modebar hidden (product feel)."""
    where.plotly_chart(_style(fig, height), width="stretch", config=CHART_CONFIG)


# diverging correlation scale: negative=cool blue, ~0=recessive gray, positive=warm red
CORR_SCALE = [[0.0, "#3987e5"], [0.5, "#2a2a26"], [1.0, "#e66767"]]


def corr_heatmap(dfc, order, labels):
    """Pairwise-Pearson heatmap over the given columns, r printed in each cell.
    Uses the dataviz diverging pair (blue↔red, gray midpoint) — sign is the story."""
    import numpy as np

    m = dfc[order].corr()  # pairwise-complete by default
    z = m.to_numpy()
    txt = [[("" if np.isnan(v) else f"{v:+.2f}") for v in row] for row in z]
    labs = [labels[c] for c in order]
    fig = go.Figure(go.Heatmap(
        z=z, x=labs, y=labs, zmid=0, zmin=-1, zmax=1, colorscale=CORR_SCALE,
        text=txt, texttemplate="%{text}",
        textfont=dict(family="JetBrains Mono, monospace", size=10, color=INK),
        hovertemplate="%{y}  ×  %{x}<br>r = %{z:.2f}<extra></extra>",
        xgap=2, ygap=2,
        colorbar=dict(title="r", outlinewidth=0, thickness=10,
                      tickfont=dict(color=MUTED, size=10,
                                    family="JetBrains Mono, monospace"))))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, tickangle=-35)
    return fig


# --- guard: DB exists? -------------------------------------------------------
if not config.DB_PATH.exists():
    st.title("🏃 Garmin Coach")
    st.warning(
        f"No database at `{config.DB_PATH}`.\n\n"
        "Seed sample data:  `python scripts/seed_sample_data.py`  then relaunch with "
        "`GARMIN_DB=data/garmin_sample.db streamlit run garmin_coach/dashboard/app.py`\n\n"
        "Or pull real data:  `python scripts/bootstrap_login.py` then "
        "`python -m garmin_coach.ingest.run --backfill 120`"
    )
    st.stop()

# --- branded hero ------------------------------------------------------------
_last = q("SELECT MAX(date) d FROM activities")
last_sync = _last.iloc[0]["d"] if len(_last) and _last.iloc[0]["d"] else "—"
st.markdown(f"""
<div class="hero">
  <div class="hero-l">
    <div class="mark"></div>
    <div>
      <div class="hero-title">GARMIN <b>COACH</b></div>
      <div class="hero-sub">Endurance telemetry · local &amp; private</div>
    </div>
  </div>
  <div class="hero-r">
    <span class="pill"><span class="dot"></span>live · {config.DB_PATH.name}</span>
    <span class="pill">last activity {last_sync}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# --- active training plan banner with progress-to-race -----------------------
_plan = q("SELECT name, level, start_date, end_date FROM training_plan LIMIT 1")
if len(_plan):
    p = _plan.iloc[0]
    prog = q("SELECT CAST(julianday('now')-julianday(?) AS FLOAT) e, "
             "CAST(julianday(?)-julianday(?) AS FLOAT) tot, "
             "CAST(julianday(?)-julianday('now') AS INT) days_left",
             (p["start_date"], p["end_date"], p["start_date"], p["end_date"])).iloc[0]
    pct = max(0.0, min(100.0, (prog["e"] / prog["tot"] * 100) if prog["tot"] else 0))
    left = int(prog["days_left"]) if pd.notna(prog["days_left"]) else None
    meta = (f"<b>{left}</b> days to race day · {p['end_date']}" if left is not None
            else p["end_date"])
    st.markdown(f"""
    <div class="plan">
      <div><span class="plan-goal">◎ <em>{p['name']}</em></span>
           <span class="plan-level">{p['level']}</span></div>
      <div class="plan-bar"><div class="plan-track">
        <div class="plan-fill" style="width:{pct:.0f}%"></div></div></div>
      <div class="plan-meta">{meta}</div>
    </div>
    """, unsafe_allow_html=True)

tabs = st.tabs(
    ["Overview", "Running trends", "Load & recovery", "Fitness & Strength",
     "Activity drill-down", "Sleep / HRV / stress", "Correlations", "AI coach"]
)

# ============================================================ OVERVIEW
with tabs[0]:
    # --- pull latest data ----------------------------------------------------
    # Same CLI the nightly scheduled task runs, so the button can't drift into
    # its own behaviour. Ingestion is the only writer; it stays in a subprocess.
    def _do_pull(mode: str, **kw) -> None:
        with st.status("Pulling from Garmin…", expanded=True) as status:
            log_box = st.empty()
            lines: list[str] = []
            result = None
            for kind, payload in launcher.run_ingest(mode, **kw):
                if kind == "log":
                    lines.append(str(payload))
                    log_box.code("\n".join(lines[-14:]), language="text")
                else:
                    result = payload

            if result is None:
                status.update(label="Pull produced no result.", state="error")
                return
            if result.ok:
                status.update(label=result.summary(), state="complete",
                              expanded=False)
                # Charts read through @st.cache_data; without this they would
                # keep serving pre-pull rows for up to the 5-minute TTL.
                st.session_state.last_pull_msg = result.summary()
                st.cache_data.clear()
                st.rerun()
            else:
                status.update(label=result.summary(), state="error")
                st.session_state.last_pull_msg = None

    if msg := st.session_state.pop("last_pull_msg", None):
        st.success(msg, icon="✅")

    pull_l, pull_r = st.columns([3, 1])
    _seen = q("SELECT MAX(date) d FROM activities").iloc[0]["d"]
    _pulled = launcher.last_ingest_at()
    pull_l.caption(
        f"Latest activity **{_seen or '—'}**"
        + (f" · last pull {str(_pulled).replace('T', ' ')}" if _pulled else "")
        + f" · today is {pd.Timestamp.today().date()}"
    )
    _busy = launcher.is_running()
    if pull_r.button("⟳ Pull my latest data", type="primary", disabled=_busy,
                     use_container_width=True,
                     help="Re-pulls the trailing days plus recent activities — "
                          "the same job the nightly task runs. Takes a minute or two."):
        _do_pull("nightly")
    if _busy:
        st.caption("A pull is already running (started elsewhere). "
                   "The button unlocks when it finishes.")

    with st.expander("More pull options"):
        st.caption("Backfill re-pulls a longer window; it skips dates already "
                   "marked complete in `ingest_log`, so it is safe to re-run.")
        bf_c1, bf_c2, bf_c3 = st.columns([2, 2, 1])
        bf_days = bf_c1.number_input("Backfill days", 1, 365, 14)
        bf_streams = bf_c2.checkbox("Include per-second streams", value=True,
                                    help="Slower. Runs only; needed for "
                                         "decoupling and pace/HR drill-downs.")
        if bf_c3.button("Backfill", disabled=_busy, use_container_width=True):
            _do_pull("backfill", days=int(bf_days), with_streams=bf_streams)

    acwr, zones = _acwr_and_zones()
    vol = q("SELECT COALESCE(SUM(distance_m),0)/1000.0 km FROM activities "
            "WHERE date >= date('now','-6 days') AND type LIKE '%running%'").iloc[0]["km"]
    hrv_now = q("SELECT last_night_avg FROM hrv WHERE last_night_avg IS NOT NULL "
                "ORDER BY date DESC LIMIT 1")
    rdy = q("SELECT AVG(score) s FROM readiness WHERE date >= date('now','-6 days')")

    # headline KPI tiles — status-coloured where a value carries a verdict
    _acwr_tone = {"OPTIMAL": "good", "LOW": "info", "ELEVATED": "warn",
                  "HIGH": "bad", "UNKNOWN": "default"}.get(acwr["flag"], "default")
    hrv_v = int(hrv_now.iloc[0, 0]) if len(hrv_now) else None
    rdy_v = rdy.iloc[0]["s"]
    # 5th tile prefers Garmin Readiness; falls back to resting HR when the device
    # doesn't report readiness (this account) so the slot is never a dead "—".
    if rdy_v is not None:
        rec_tile = tile("Readiness", f"{rdy_v:.0f}", "/100", "7-day average",
                        "good" if rdy_v >= 75 else "warn" if rdy_v >= 50 else "bad")
    else:
        rhr7 = q("SELECT resting_hr v FROM rhr WHERE resting_hr IS NOT NULL "
                 "ORDER BY date DESC LIMIT 1")
        rhr_base = q("SELECT AVG(resting_hr) v FROM rhr WHERE date >= date('now','-27 days')")
        rv = int(rhr7.iloc[0]["v"]) if len(rhr7) else None
        base = rhr_base.iloc[0]["v"]
        sub = ("below 4-wk avg ✓" if rv and base and rv < base
               else "above 4-wk avg" if rv and base else "resting heart rate")
        rec_tile = tile("Resting HR", rv if rv is not None else "—", "bpm", sub,
                        "good" if (rv and base and rv < base) else "default")
    # Acute-load tile: use Garmin training_load if the device provides it (unitless
    # load points); otherwise it's a distance proxy — label it honestly in km so the
    # number isn't mistaken for a running-only or unitless "load".
    has_load = q("SELECT COUNT(training_load) n FROM activities").iloc[0]["n"] > 0
    if has_load:
        acute_tile = tile("Acute load", f"{acwr['acute_7d']:g}", "",
                          "7-day training load", "info")
    else:
        acute_tile = tile("Acute distance", f"{acwr['acute_7d']:g}", "km",
                          "all sports · 7-day load proxy", "info")
    kpi_row([
        tile("7-day volume", f"{vol:.1f}", "km", "running · rolling 7 days", "accent"),
        acute_tile,
        tile("ACWR", acwr["acwr"] if acwr["acwr"] else "—", "",
             acwr["flag"].title() + " · aim 0.8–1.3", _acwr_tone),
        tile("HRV last night", hrv_v if hrv_v is not None else "—", "ms",
             "overnight avg", "default"),
        rec_tile,
    ])

    section("Daily training load", "Workload",
            "Per-day load (Garmin training load, or distance where absent). "
            "The dashed line is your period average — bars above it are your bigger days.")
    load = q("SELECT date, COALESCE(SUM(COALESCE(training_load,distance_m/1000.0)),0) load "
             "FROM activities GROUP BY date ORDER BY date")
    if len(load):
        load["date"] = pd.to_datetime(load["date"])
        avg = load["load"][load["load"] > 0].mean()
        # highlight the most-recent day that actually has load ("now")
        _nz = load["load"].to_numpy().nonzero()[0]
        hot = int(_nz[-1]) if len(_nz) else len(load) - 1
        colors = [ACCENT if i == hot else PALETTE[0] for i in range(len(load))]
        fig = go.Figure(go.Bar(x=load["date"], y=load["load"], marker_color=colors,
                               hovertemplate="%{x|%b %d}<br>load %{y:.0f}<extra></extra>"))
        if pd.notna(avg):
            fig.add_hline(y=avg, line_dash="dot", line_color=MUTED, line_width=1.3,
                          annotation_text=f"avg {avg:.0f}", annotation_position="top left",
                          annotation_font=dict(color=MUTED, size=10,
                                               family="JetBrains Mono, monospace"))
        show(fig, 320)

    section("Easy / hard balance", "Polarised training · last 28 days",
            "Endurance thrives on ~80% easy / 20% hard. The marker shows the 80% "
            "target; the zone strip below shows where your minutes actually went.")
    if zones["easy_pct"] is not None:
        easy, hard = zones["easy_pct"], zones["hard_pct"]
        on_target = easy >= 78
        kpi_row([
            tile("Easy (Z1–Z2)", f"{easy:g}", "%",
                 "on target" if on_target else "below 80% target",
                 "good" if on_target else "warn"),
            tile("Hard (Z3–Z5)", f"{hard:g}", "%", "quality work", "info"),
            tile("Logged in zone", f"{zones['total_min']:g}", "min",
                 "last 28 days", "default"),
        ])
        # single 100%-stacked split bar with an 80% target tick
        split = go.Figure()
        split.add_trace(go.Bar(y=["split"], x=[easy], name="Easy", orientation="h",
                               marker_color=PALETTE[4], marker_cornerradius=0,
                               hovertemplate=f"Easy {easy:g}%<extra></extra>"))
        split.add_trace(go.Bar(y=["split"], x=[hard], name="Hard", orientation="h",
                               marker_color=PALETTE[5], marker_cornerradius=0,
                               hovertemplate=f"Hard {hard:g}%<extra></extra>"))
        split.add_vline(x=80, line_color=ACCENT, line_width=2,
                        annotation_text="80% target", annotation_position="top",
                        annotation_font=dict(color=ACCENT, size=10,
                                             family="JetBrains Mono, monospace"))
        split.update_layout(barmode="stack", showlegend=True, xaxis=dict(range=[0, 100]))
        split.update_yaxes(showticklabels=False)
        split.update_xaxes(ticksuffix="%")
        show(split, 150)
        # zone-by-zone minutes, horizontal, directly labelled
        zdf = pd.DataFrame({"zone": ["Z1", "Z2", "Z3", "Z4", "Z5"],
                            "minutes": [zones["seconds"][f"z{i}"] / 60 for i in range(1, 6)]})
        zfig = px.bar(zdf, x="minutes", y="zone", orientation="h", color="zone",
                      color_discrete_sequence=ZONE_COLORS, text="minutes")
        zfig.update_traces(texttemplate="%{text:.0f}m", textposition="outside",
                           textfont=dict(color=INK2, size=11,
                                         family="JetBrains Mono, monospace"),
                           cliponaxis=False)
        zfig.update_layout(showlegend=False)
        zfig.update_yaxes(categoryorder="array", categoryarray=["Z5", "Z4", "Z3", "Z2", "Z1"])
        zfig.update_xaxes(title="minutes in zone")
        show(zfig, 240)
    else:
        st.info("No time-in-zone data yet (needs activities with HR zones).")

    # Plan: what's next & how recent prescriptions went
    upcoming = q("""SELECT date, title FROM planned_workouts
                    WHERE sport='running' AND date >= date('now')
                    ORDER BY date LIMIT 7""")
    recent_plan = q("""
        SELECT p.date, p.title AS planned,
               (SELECT ROUND(a.distance_m/1000.0,1) FROM activities a
                WHERE a.date=p.date AND a.type LIKE '%running%' LIMIT 1) AS actual_km
        FROM planned_workouts p
        WHERE p.sport='running' AND p.date < date('now')
              AND p.date >= date('now','-14 days')
        ORDER BY p.date DESC""")
    if len(upcoming) or len(recent_plan):
        section("Training plan", "Prescribed vs actual")
        pc1, pc2 = st.columns(2)
        if len(upcoming):
            pc1.caption("Upcoming sessions")
            pc1.dataframe(upcoming, width='stretch', hide_index=True)
        if len(recent_plan):
            recent_plan["done"] = recent_plan["actual_km"].apply(
                lambda k: f"✅ {k} km" if pd.notna(k) else "❌ missed")
            pc2.caption("Recent planned vs actual")
            pc2.dataframe(recent_plan[["date", "planned", "done"]],
                          width='stretch', hide_index=True)

# ============================================================ RUNNING TRENDS
with tabs[1]:
    section("Weekly mileage", "Volume", "Running kilometres per calendar week. "
            "The dashed line marks your average week; the latest week is highlighted.")
    wk = q("SELECT strftime('%Y-%W', date) week, MIN(date) wk_start, "
           "SUM(distance_m)/1000.0 km FROM activities WHERE type LIKE '%running%' "
           "GROUP BY week ORDER BY week")
    if len(wk):
        wavg = wk["km"].mean()
        _wnz = wk["km"].to_numpy().nonzero()[0]
        whot = int(_wnz[-1]) if len(_wnz) else len(wk) - 1
        wcolors = [ACCENT if i == whot else PALETTE[0] for i in range(len(wk))]
        wfig = go.Figure(go.Bar(x=wk["wk_start"], y=wk["km"], marker_color=wcolors,
                                text=wk["km"], texttemplate="%{text:.0f}",
                                textposition="outside", cliponaxis=False,
                                textfont=dict(color=MUTED, size=10,
                                              family="JetBrains Mono, monospace"),
                                hovertemplate="week of %{x|%b %d}<br>%{y:.1f} km<extra></extra>"))
        wfig.add_hline(y=wavg, line_dash="dot", line_color=MUTED, line_width=1.3,
                       annotation_text=f"avg {wavg:.0f} km", annotation_position="top left",
                       annotation_font=dict(color=MUTED, size=10,
                                            family="JetBrains Mono, monospace"))
        wfig.update_yaxes(title="km")
        show(wfig, 320)

    section("Aerobic engine", "Pace at a fixed heart rate",
            "Same effort, faster pace = a stronger aerobic engine. A falling line "
            "means you're getting fitter. Defaults to your easy-run HR band.")
    zn = get_zones()
    # Default to the athlete's easy band, derived from their live Garmin zones, so
    # the chart just renders. The manual override is tucked into an expander.
    hr_low = max(100, zn["z2_ceiling"] - 15)
    hr_high = zn["z2_ceiling"]
    with st.expander("⚙ Adjust HR band"):
        st.caption(f"Zones from {zn['source']} · easy ceiling {zn['z2_ceiling']} · "
                   f"LTHR {zn['lthr']} · max {zn['max_hr']} bpm")
        cc1, cc2 = st.columns(2)
        hr_low = cc1.number_input("HR band low", 100, 210, hr_low)
        hr_high = cc2.number_input("HR band high", 100, 210, hr_high)
    conn = db.connect_ro()
    pah = pd.DataFrame(metrics.pace_at_hr(conn, int(hr_low), int(hr_high), months=12))
    conn.close()
    st.caption(f"Easy band {int(hr_low)}–{int(hr_high)} bpm"
               + (f" · {int(pah['runs'].sum())} runs" if len(pah) and 'runs' in pah else ""))
    if len(pah):
        fig = go.Figure(go.Scatter(
            x=pah["month"], y=pah["avg_pace_s_per_km"], mode="lines+markers",
            line=dict(color=PALETTE[0], width=2.4),
            marker=dict(size=8, color=PALETTE[0]),
            customdata=pah["avg_pace_s_per_km"].apply(fmt_pace),
            hovertemplate="%{x}<br>%{customdata}<extra></extra>"))
        # highlight latest month in the signature accent ("now")
        last = pah.iloc[-1]
        fig.add_trace(go.Scatter(x=[last["month"]], y=[last["avg_pace_s_per_km"]],
                                 mode="markers", showlegend=False,
                                 marker=dict(size=13, color=ACCENT,
                                             line=dict(color=PLANE, width=2)),
                                 hoverinfo="skip"))
        # trend delta annotation (first → last)
        if len(pah) > 1:
            delta = pah.iloc[0]["avg_pace_s_per_km"] - last["avg_pace_s_per_km"]
            faster = delta > 0
            fig.add_annotation(x=last["month"], y=last["avg_pace_s_per_km"],
                               text=f"{'▼' if faster else '▲'} {abs(int(delta))}s/km "
                                    f"{'faster' if faster else 'slower'}",
                               showarrow=False, yshift=22, xshift=-6,
                               font=dict(color=ACCENT if faster else STATUS["serious"],
                                         size=11, family="JetBrains Mono, monospace"))
        pace_axis(fig, pah["avg_pace_s_per_km"])
        fig.update_yaxes(autorange="reversed")
        show(fig)
    else:
        st.info("No runs in that HR band yet. Widen the band or backfill more history.")

    section("Pace vs distance", "Every run · coloured by average heart rate",
            "Lower is faster. Cooler dots are easy-HR runs, warmer dots are hard "
            "efforts — points drifting down-and-cool over time is the goal.")
    runs = q("SELECT distance_m/1000.0 km, avg_pace_s_per_km pace, avg_hr, date "
             "FROM activities WHERE type LIKE '%running%' AND avg_pace_s_per_km IS NOT NULL")
    if len(runs):
        runs["pace_label"] = runs["pace"].apply(fmt_pace)
        fig = px.scatter(runs, x="km", y="pace", color="avg_hr",
                         color_continuous_scale=HR_SCALE,
                         labels={"avg_hr": "avg HR", "km": "distance (km)"},
                         hover_data={"date": True, "pace_label": True, "pace": False})
        fig.update_traces(marker=dict(size=10, opacity=0.85,
                                      line=dict(width=1, color=PLANE)))
        pace_axis(fig, runs["pace"])
        fig.update_yaxes(autorange="reversed")
        show(fig)

    section("Running economy", "Cadence vs pace · coloured by heart rate",
            "Higher cadence (more, lighter steps) usually means less braking and "
            "overstriding. Faster paces should ride on higher cadence, not just more HR.")
    cad = q("SELECT avg_cadence cadence, avg_pace_s_per_km pace, avg_hr, date "
            "FROM activities WHERE type LIKE '%running%' AND avg_cadence IS NOT NULL "
            "AND avg_pace_s_per_km IS NOT NULL")
    if len(cad) >= 3:
        cad["pace_label"] = cad["pace"].apply(fmt_pace)
        fig = px.scatter(cad, x="cadence", y="pace", color="avg_hr",
                         color_continuous_scale=HR_SCALE,
                         labels={"cadence": "cadence (spm)", "avg_hr": "avg HR"},
                         hover_data={"date": True, "pace_label": True, "pace": False})
        fig.update_traces(marker=dict(size=11, opacity=0.85, line=dict(width=1, color=PLANE)))
        pace_axis(fig, cad["pace"])
        fig.update_yaxes(autorange="reversed")
        show(fig, 320)
        r = cad["cadence"].corr(cad["pace"])
        st.caption(f"Pearson r = {r:+.2f} across {len(cad)} runs "
                   "(negative = faster pace at higher cadence). Small sample — indicative only.")
    else:
        st.info("Need a few more runs with cadence to chart running economy.")

    vo2 = q("SELECT date, vo2max FROM training_status WHERE vo2max IS NOT NULL ORDER BY date")
    if len(vo2):
        section("VO₂max trend", "Estimated aerobic ceiling")
        show(px.line(vo2, x="date", y="vo2max", markers=True), 260)

# ============================================================ LOAD & RECOVERY
with tabs[2]:
    section("Injury-risk ratio", "Acute : chronic workload (ACWR)",
            "Acute (last 7 days) vs chronic (28-day average) load. The green band "
            "0.8–1.3 is the sweet spot; drifting above 1.5 means load is spiking "
            "faster than your body is adapting.")
    daily = q("SELECT date, COALESCE(SUM(COALESCE(training_load,distance_m/1000.0)),0) load "
              "FROM activities GROUP BY date ORDER BY date")
    if len(daily):
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.set_index("date").asfreq("D", fill_value=0)
        daily["acute"] = daily["load"].rolling(7, min_periods=1).sum()
        daily["chronic"] = daily["load"].rolling(28, min_periods=1).sum() / 4
        daily["acwr"] = daily["acute"] / daily["chronic"].replace(0, pd.NA)
        fig = go.Figure()
        # risk bands: undertrained / optimal / elevated / high
        fig.add_hrect(y0=0, y1=0.8, fillcolor=PALETTE[0], opacity=0.06, line_width=0)
        fig.add_hrect(y0=0.8, y1=1.3, fillcolor=STATUS["good"], opacity=0.14, line_width=0,
                      annotation_text="optimal 0.8–1.3", annotation_position="top left",
                      annotation_font=dict(color=STATUS["good"], size=10,
                                           family="JetBrains Mono, monospace"))
        fig.add_hrect(y0=1.3, y1=1.5, fillcolor=STATUS["warning"], opacity=0.10, line_width=0)
        fig.add_hrect(y0=1.5, y1=3, fillcolor=STATUS["critical"], opacity=0.10, line_width=0)
        fig.add_trace(go.Scatter(x=daily.index, y=daily["acwr"], name="ACWR", mode="lines",
                                 line=dict(color=INK, width=2.2),
                                 hovertemplate="%{x|%b %d}<br>ACWR %{y:.2f}<extra></extra>"))
        # mark "now" in the signature accent
        cur = daily["acwr"].dropna()
        if len(cur):
            fig.add_trace(go.Scatter(x=[cur.index[-1]], y=[cur.iloc[-1]], mode="markers",
                                     showlegend=False, hoverinfo="skip",
                                     marker=dict(size=12, color=ACCENT,
                                                 line=dict(color=PLANE, width=2))))
        fig.add_hline(y=1.5, line_dash="dot", line_color=STATUS["critical"], line_width=1.3,
                      annotation_text="1.5 danger line", annotation_position="top right",
                      annotation_font=dict(color=STATUS["critical"], size=10,
                                           family="JetBrains Mono, monospace"))
        fig.update_yaxes(title="ACWR", range=[0, max(2.0, float(cur.max()) * 1.15 if len(cur) else 2)])
        fig.update_layout(showlegend=False)
        show(fig)

        section("Load balance", "Acute vs chronic (fitness vs fatigue)",
                "Acute above chronic = building fatigue; acute below = freshening up "
                "and absorbing fitness before a race.")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=daily.index, y=daily["chronic"], name="Chronic (28d avg)",
                                  mode="lines", line=dict(color=PALETTE[3], width=2.2)))
        fig2.add_trace(go.Scatter(x=daily.index, y=daily["acute"], name="Acute (7d)",
                                  mode="lines", fill="tonexty",
                                  fillcolor="rgba(57,135,229,0.12)",
                                  line=dict(color=PALETTE[0], width=2.2)))
        fig2.update_yaxes(title="load")
        show(fig2)

    section("Training stimulus", "Aerobic vs anaerobic effect · every session",
            "Each session's Training Effect: aerobic (endurance) across, anaerobic "
            "(speed/power) up. The quadrant tells you what kind of fitness you're "
            "actually building — most half-marathon work should sit in Base/Tempo.")
    te = q("SELECT type, training_effect_aerobic aer, training_effect_anaerobic anaer, "
           "duration_s/60.0 mins, date FROM activities "
           "WHERE training_effect_aerobic IS NOT NULL AND training_effect_anaerobic IS NOT NULL "
           "AND (training_effect_aerobic > 0 OR training_effect_anaerobic > 0)")
    if len(te):
        # keep the 3 most-frequent sports as distinct hues, fold the rest into
        # "Other" — a scatter stays CVD-safe at ≤4 categorical series (position
        # carries the real message; hover still shows each activity's date/type).
        te["Sport"] = te["type"].str.replace("_", " ").str.title()
        top = te["Sport"].value_counts().nlargest(3).index.tolist()
        te["Sport"] = te["Sport"].where(te["Sport"].isin(top), "Other")
        _cmap = {s: PALETTE[i] for i, s in enumerate(top)}
        _cmap["Other"] = MUTED
        fig = px.scatter(te, x="aer", y="anaer", color="Sport", size="mins",
                         size_max=26, hover_data={"date": True, "type": True, "mins": ":.0f"},
                         category_orders={"Sport": top + ["Other"]},
                         color_discrete_map=_cmap,
                         labels={"aer": "aerobic training effect",
                                 "anaer": "anaerobic training effect"})
        fig.update_traces(marker=dict(opacity=0.85, line=dict(width=1, color=PLANE)))
        # quadrant guides + labels (aerobic 2.5, anaerobic 1.0 splits)
        fig.add_vline(x=2.5, line_dash="dot", line_color=BASELINE, line_width=1)
        fig.add_hline(y=1.0, line_dash="dot", line_color=BASELINE, line_width=1)
        _q = [(1.0, 0.3, "RECOVERY"), (3.6, 0.3, "BASE / TEMPO"),
              (1.0, 1.9, "ANAEROBIC"), (3.6, 1.9, "VO₂ / RACE")]
        for qx, qy, lab in _q:
            fig.add_annotation(x=qx, y=qy, text=lab, showarrow=False,
                               font=dict(color=MUTED, size=9,
                                         family="JetBrains Mono, monospace"))
        fig.update_xaxes(range=[0, 5], title="aerobic training effect")
        fig.update_yaxes(range=[0, 2.6], title="anaerobic training effect")
        show(fig, 380)

    ts = q("SELECT date, status FROM training_status WHERE status IS NOT NULL ORDER BY date")
    if len(ts):
        section("Training status timeline", "Garmin's verdict")
        st.dataframe(ts.tail(20), width='stretch', hide_index=True)

# ============================================================ FITNESS & STRENGTH
with tabs[3]:
    section("Race predictor", "Projected finish times from current fitness",
            "Garmin's model of what you could run today at each distance. Your goal "
            "race — the Half — is highlighted.")
    fit = q("SELECT date, vo2max, vo2max_precise, fitness_age, race_5k_s, race_10k_s, "
            "race_half_s, race_marathon_s, ftp_watts, ftp_w_per_kg FROM fitness "
            "WHERE vo2max IS NOT NULL OR race_5k_s IS NOT NULL ORDER BY date")
    if len(fit):
        latest = fit.iloc[-1]

        def _hms(s):
            if pd.isna(s):
                return "—"
            s = int(s)
            h, rem = divmod(s, 3600)
            m, sec = divmod(rem, 60)
            return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

        ftp = fit.dropna(subset=["ftp_watts"])
        ftp_v = (f"{int(ftp.iloc[-1]['ftp_watts'])}", "W") if len(ftp) else ("—", "")
        kpi_row([
            tile("5K", _hms(latest["race_5k_s"])),
            tile("10K", _hms(latest["race_10k_s"])),
            tile("Half", _hms(latest["race_half_s"]), "", "goal race", "accent"),
            tile("Marathon", _hms(latest["race_marathon_s"])),
            tile("Run FTP", ftp_v[0], ftp_v[1],
                 f"{ftp.iloc[-1]['ftp_w_per_kg']} W/kg" if len(ftp) else "", "info"),
        ])

        f1, f2 = st.columns(2)
        vo2 = fit.dropna(subset=["vo2max_precise"])
        if len(vo2):
            section("VO₂max", "Aerobic ceiling", where=f1)
            show(px.line(vo2, x="date", y="vo2max_precise", markers=True), 260, where=f1)
        fa = fit.dropna(subset=["fitness_age"])
        if len(fa):
            section("Fitness age", "Physiological vs calendar", where=f2)
            show(px.line(fa, x="date", y="fitness_age", markers=True), 260, where=f2)
    else:
        st.info("No fitness metrics yet.")

    section("Personal records", "Lifetime bests")
    prs = q("SELECT label, value, activity_name, date FROM personal_records ORDER BY type_id")
    if len(prs):
        st.dataframe(prs, width='stretch', hide_index=True)

    section("Strength training", "Resistance work")
    strength = q("""
        SELECT s.exercise_name, COUNT(*) sets, MAX(s.weight_kg) top_weight_kg,
               MAX(s.reps) max_reps, MAX(a.date) last_done
        FROM strength_sets s JOIN activities a ON a.activity_id = s.activity_id
        WHERE s.exercise_name IS NOT NULL AND s.set_type='ACTIVE'
        GROUP BY s.exercise_name ORDER BY sets DESC
    """)
    if len(strength):
        st.caption(f"{len(strength)} distinct exercises tracked across your strength sessions.")
        st.dataframe(strength, width='stretch', hide_index=True)
        pick = st.selectbox("Progression for exercise",
                            strength["exercise_name"].tolist())
        prog = q("""
            SELECT a.date, MAX(s.weight_kg) weight_kg, MAX(s.reps) reps
            FROM strength_sets s JOIN activities a ON a.activity_id = s.activity_id
            WHERE s.exercise_name = ? AND s.set_type='ACTIVE'
            GROUP BY a.date ORDER BY a.date
        """, (pick,))
        if len(prog) and prog["weight_kg"].notna().any():
            show(px.line(prog, x="date", y="weight_kg", markers=True), 280)
    else:
        st.info("No strength-set data yet.")

# ============================================================ ACTIVITY DRILL-DOWN
with tabs[4]:
    acts = q("SELECT activity_id, date, name, type, distance_m/1000.0 km, "
             "avg_pace_s_per_km pace, avg_hr, decoupling_pct FROM activities "
             "ORDER BY date DESC LIMIT 200")
    if not len(acts):
        st.info("No activities yet.")
    else:
        acts["label"] = acts.apply(
            lambda r: f"{r['date']} · {r['name'] or r['type']} · {r['km']:.1f}km · {fmt_pace(r['pace'])}",
            axis=1)
        pick = st.selectbox("Pick an activity", acts["label"])
        row = acts[acts["label"] == pick].iloc[0]
        aid = int(row["activity_id"])
        dec = row["decoupling_pct"]
        dec_tone = "default" if pd.isna(dec) else (
            "good" if dec < 5 else "warn" if dec < 10 else "bad")
        dec_sub = "" if pd.isna(dec) else (
            "strong aerobic base" if dec < 5 else "moderate drift" if dec < 10 else "high drift")
        kpi_row([
            tile("Distance", f"{row['km']:.2f}", "km", "", "accent"),
            tile("Avg pace", fmt_pace(row["pace"]).replace("/km", ""), "/km"),
            tile("Avg HR", int(row["avg_hr"]) if pd.notna(row["avg_hr"]) else "—", "bpm"),
            tile("Decoupling", f"{dec:g}" if pd.notna(dec) else "—", "%", dec_sub, dec_tone),
        ])

        wx = q("SELECT temp_c, feels_like_c, humidity, weather_desc FROM activities "
               "WHERE activity_id=?", (aid,))
        if len(wx) and pd.notna(wx.iloc[0]["temp_c"]):
            w = wx.iloc[0]
            st.caption(f"🌡️ {w['temp_c']}°C (feels {w['feels_like_c']}°C) · "
                       f"{int(w['humidity']) if pd.notna(w['humidity']) else '—'}% humidity · "
                       f"{w['weather_desc'] or ''}")

        # Strength session? show the sets.
        sets = q("SELECT set_idx+1 setn, exercise_name, reps, weight_kg, ROUND(duration_s) dur_s "
                 "FROM strength_sets WHERE activity_id=? AND set_type='ACTIVE' "
                 "AND exercise_name IS NOT NULL ORDER BY set_idx", (aid,))
        if len(sets):
            section("Sets", "Session breakdown")
            st.dataframe(sets, width='stretch', hide_index=True)

        stream = q("SELECT offset_s/60.0 min, hr, speed_mps FROM activity_streams "
                   "WHERE activity_id=? ORDER BY offset_s", (aid,))
        if len(stream):
            stream["pace"] = 1000.0 / stream["speed_mps"].replace(0, pd.NA)
            stream.loc[stream["pace"] > 900, "pace"] = pd.NA  # clip stopped/spike moments
            section("Effort trace", "Heart rate & pace over the run",
                    "Stacked single-axis (never dual-axis): heart rate on top, pace "
                    "below — read them together to see where effort and speed diverged.")
            # Two stacked single-axis charts (never dual-axis): HR, then pace.
            hr_fig = px.line(stream, x="min", y="hr")
            hr_fig.update_traces(line=dict(color=PALETTE[7], width=1.8),
                                 hovertemplate="%{x:.0f} min<br>%{y:.0f} bpm<extra></extra>")
            hr_fig.update_yaxes(title="heart rate")
            hr_fig.update_xaxes(title="")
            show(hr_fig, 220)

            pc = stream.dropna(subset=["pace"])
            if len(pc):
                p_fig = px.line(pc, x="min", y="pace")
                p_fig.update_traces(line=dict(color=PALETTE[0], width=1.8),
                                    customdata=pc["pace"].apply(fmt_pace),
                                    hovertemplate="%{x:.0f} min<br>%{customdata}<extra></extra>")
                pace_axis(p_fig, pc["pace"])
                p_fig.update_yaxes(autorange="reversed")
                p_fig.update_xaxes(title="minutes")
                show(p_fig, 220)

        zc = q("SELECT z1_s,z2_s,z3_s,z4_s,z5_s FROM activities WHERE activity_id=?", (aid,))
        if len(zc) and zc.iloc[0].sum():
            section("Time in zone", "Where this run's effort lived")
            zdf = pd.DataFrame({"zone": ["Z1", "Z2", "Z3", "Z4", "Z5"],
                                "min": [zc.iloc[0][f"z{i}_s"] / 60 if pd.notna(zc.iloc[0][f"z{i}_s"]) else 0
                                        for i in range(1, 6)]})
            fig = px.bar(zdf, x="min", y="zone", orientation="h", color="zone",
                         color_discrete_sequence=ZONE_COLORS, text="min")
            fig.update_traces(texttemplate="%{text:.0f}m", textposition="outside",
                              cliponaxis=False,
                              textfont=dict(color=INK2, size=11,
                                            family="JetBrains Mono, monospace"))
            fig.update_layout(showlegend=False)
            fig.update_yaxes(categoryorder="array", categoryarray=["Z5", "Z4", "Z3", "Z2", "Z1"])
            show(fig, 220)

        splits = q("SELECT split_idx+1 split, avg_pace_s_per_km pace, avg_hr FROM "
                   "activity_splits WHERE activity_id=? ORDER BY split_idx", (aid,))
        sp = splits.dropna(subset=["pace"])
        if len(sp) >= 2:
            section("Splits", "Per-kilometre pace & drift",
                    "Bars are per-split pace (coloured by heart rate); a negative "
                    "split — second half faster than the first — is the hallmark of "
                    "even, controlled pacing.")
            mid = len(sp) // 2
            first_half = sp["pace"].iloc[:mid].mean()
            second_half = sp["pace"].iloc[mid:].mean()
            neg = second_half < first_half
            drift = second_half - first_half
            k1, k2, k3 = st.columns(3)
            kpi_row([tile("1st half", fmt_pace(first_half).replace("/km", ""), "/km")], where=k1)
            kpi_row([tile("2nd half", fmt_pace(second_half).replace("/km", ""), "/km")], where=k2)
            kpi_row([tile("Split", ("−" if neg else "+") + fmt_pace(abs(drift)).replace("/km", ""),
                          "/km", "negative — well paced ✓" if neg else "positive — faded",
                          "good" if neg else "warn")], where=k3)
            # line + HR-coloured markers (up = faster); reversed axis handles the
            # non-zero pace baseline that a bar chart can't.
            sfig = go.Figure(go.Scatter(
                x=sp["split"], y=sp["pace"], mode="lines+markers",
                line=dict(color=BASELINE, width=2),
                marker=dict(size=13, color=sp["avg_hr"], colorscale=HR_SCALE,
                            line=dict(width=1, color=PLANE),
                            colorbar=dict(title="HR", outlinewidth=0, thickness=10,
                                          tickfont=dict(color=MUTED, size=10,
                                                        family="JetBrains Mono, monospace"))),
                customdata=sp["pace"].apply(fmt_pace),
                hovertemplate="split %{x}<br>%{customdata}<extra></extra>"))
            pace_axis(sfig, sp["pace"])
            sfig.update_yaxes(autorange="reversed")
            sfig.update_xaxes(title="split (km)", dtick=1)
            show(sfig, 260)
        elif len(splits):
            splits["pace"] = splits["pace"].apply(fmt_pace)
            st.dataframe(splits, width='stretch', hide_index=True)

# ============================================================ SLEEP / HRV / STRESS
with tabs[5]:
    section("Heart-rate variability", "Nightly HRV vs your personal baseline",
            "Inside the green band = balanced recovery. Nights below it flag "
            "accumulated fatigue or stress — ease off before it compounds.")
    hrv = q("SELECT date, last_night_avg, baseline_low, baseline_high FROM hrv ORDER BY date")
    if len(hrv):
        hrv["date"] = pd.to_datetime(hrv["date"])
        fig = go.Figure()
        if hrv["baseline_high"].notna().any():
            fig.add_trace(go.Scatter(x=hrv["date"], y=hrv["baseline_high"], line=dict(width=0),
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=hrv["date"], y=hrv["baseline_low"], fill="tonexty",
                                     fillcolor="rgba(12,163,12,0.14)", line=dict(width=0),
                                     name="baseline", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=hrv["date"], y=hrv["last_night_avg"], name="HRV",
                                 mode="lines", line=dict(color=INK, width=2.2),
                                 hovertemplate="%{x|%b %d}<br>%{y:.0f} ms<extra></extra>"))
        hv = hrv.dropna(subset=["last_night_avg"])
        if len(hv):
            fig.add_trace(go.Scatter(x=[hv["date"].iloc[-1]], y=[hv["last_night_avg"].iloc[-1]],
                                     mode="markers", showlegend=False, hoverinfo="skip",
                                     marker=dict(size=12, color=ACCENT,
                                                 line=dict(color=PLANE, width=2))))
        fig.update_yaxes(title="HRV (ms)")
        show(fig)

    c1, c2 = st.columns(2)
    sleep = q("SELECT date, deep_s/3600.0 deep, light_s/3600.0 light, rem_s/3600.0 rem "
              "FROM sleep ORDER BY date")
    if len(sleep):
        sm = sleep.melt("date", var_name="stage", value_name="hours")
        # deep→light→rem as one blue ramp (sequential sleep-depth, not categorical hues)
        fig = px.area(sm, x="date", y="hours", color="stage",
                      color_discrete_sequence=["#184f95", "#3987e5", "#9085e9"])
        fig.update_traces(line=dict(width=0))
        section("Sleep stages", "Hours by stage", where=c1)
        show(fig, 280, where=c1)
    rhr = q("SELECT date, resting_hr FROM rhr ORDER BY date")
    if len(rhr):
        section("Resting heart rate", "Lower = more recovered", where=c2)
        rfig = px.line(rhr, x="date", y="resting_hr", markers=False)
        rfig.update_traces(line=dict(color=PALETTE[7], width=2.2))
        rfig.update_yaxes(title="bpm")
        show(rfig, 280, where=c2)

    stress = q("SELECT date, avg_stress FROM stress WHERE avg_stress IS NOT NULL ORDER BY date")
    if len(stress):
        section("Daily stress", "All-day average stress (0–100)",
                "Garmin's stress score from HR variability. Bands: rest, low, "
                "medium, high. Persistently elevated stress blunts recovery and "
                "adaptation — watch for it stacking on hard-training weeks.")
        stress["date"] = pd.to_datetime(stress["date"])
        sfig = go.Figure()
        for y0, y1, col in [(0, 25, PALETTE[4]), (25, 50, STATUS["warning"]),
                            (50, 75, STATUS["serious"]), (75, 100, STATUS["critical"])]:
            sfig.add_hrect(y0=y0, y1=y1, fillcolor=col, opacity=0.07, line_width=0)
        sfig.add_trace(go.Scatter(x=stress["date"], y=stress["avg_stress"], mode="lines",
                                  line=dict(color=INK, width=2.2),
                                  hovertemplate="%{x|%b %d}<br>stress %{y:.0f}<extra></extra>"))
        sfig.update_yaxes(title="stress", range=[0, 100])
        show(sfig, 260)

    bb = q("SELECT date, high, low, charged, drained FROM body_battery "
           "WHERE high IS NOT NULL ORDER BY date")
    if len(bb):
        section("Body Battery", "Daily energy range (reserve you woke with → spent)")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=bb["date"], y=bb["high"], name="high", mode="lines",
                                 line=dict(color=PALETTE[4], width=2.2)))
        fig.add_trace(go.Scatter(x=bb["date"], y=bb["low"], name="low", fill="tonexty",
                                 fillcolor="rgba(57,135,229,0.12)", mode="lines",
                                 line=dict(color=PALETTE[0], width=2.2)))
        show(fig, 260)

        bc = bb.dropna(subset=["charged", "drained"])
        if len(bc):
            section("Charge vs drain", "Energy recovered (up) vs spent (down) each day",
                    "Days that drain more than they charge are net-negative — a few "
                    "in a row is a recovery-debt warning.")
            bfig = go.Figure()
            bfig.add_trace(go.Bar(x=bc["date"], y=bc["charged"], name="charged",
                                  marker_color=PALETTE[4],
                                  hovertemplate="%{x|%b %d}<br>+%{y}<extra></extra>"))
            bfig.add_trace(go.Bar(x=bc["date"], y=-bc["drained"], name="drained",
                                  marker_color=PALETTE[5],
                                  hovertemplate="%{x|%b %d}<br>%{y}<extra></extra>"))
            bfig.add_hline(y=0, line_color=BASELINE, line_width=1)
            bfig.update_layout(barmode="relative")
            bfig.update_yaxes(title="Δ body battery")
            show(bfig, 260)

    hr_days = q("SELECT DISTINCT date FROM intraday_hr ORDER BY date DESC")
    if len(hr_days):
        section("All-day heart rate", "Minute-by-minute for a single day")
        day = st.selectbox("Day", hr_days["date"].tolist())
        ihr = q("SELECT offset_s/3600.0 hours, hr FROM intraday_hr WHERE date=? ORDER BY offset_s", (day,))
        if len(ihr):
            fig = px.line(ihr, x="hours", y="hr")
            fig.update_traces(line=dict(color=PALETTE[0], width=1.6))
            fig.update_xaxes(title="hours into day")
            fig.update_yaxes(title="bpm")
            show(fig, 260)

# ============================================================ CORRELATIONS
with tabs[6]:
    # one daily feature frame joining every populated recovery / load / performance
    # signal, reused by both the matrix and the scatter explorer.
    feat = q("""
        WITH day_act AS (
            SELECT date,
                   COALESCE(SUM(COALESCE(training_load, distance_m/1000.0)),0) AS run_load,
                   MIN(avg_pace_s_per_km) AS pace
            FROM activities WHERE type LIKE '%running%' AND avg_pace_s_per_km IS NOT NULL
            GROUP BY date)
        SELECT d.date,
               s.total_sleep_s/3600.0 AS sleep_h, s.sleep_score AS sleep_score,
               h.last_night_avg AS hrv, st.avg_stress AS stress,
               r.resting_hr AS rhr, bb.charged AS bb_charged, bb.drained AS bb_drained,
               da.run_load AS run_load, da.pace AS pace
        FROM (SELECT date FROM sleep UNION SELECT date FROM hrv UNION SELECT date FROM stress
              UNION SELECT date FROM rhr UNION SELECT date FROM body_battery) d
        LEFT JOIN sleep s ON s.date=d.date
        LEFT JOIN hrv h ON h.date=d.date
        LEFT JOIN stress st ON st.date=d.date
        LEFT JOIN rhr r ON r.date=d.date
        LEFT JOIN body_battery bb ON bb.date=d.date
        LEFT JOIN day_act da ON da.date=d.date
        ORDER BY d.date
    """)
    LB = {"sleep_h": "Sleep h", "sleep_score": "Sleep score", "hrv": "HRV",
          "stress": "Stress", "rhr": "Rest HR", "bb_charged": "BB charge",
          "bb_drained": "BB drain", "run_load": "Run load", "pace": "Run pace"}

    section("Correlation matrix", "What moves with what · daily signals",
            "Pearson r between every recovery, load and performance signal. "
            "Blue = moves opposite, red = moves together, grey ≈ unrelated. "
            "Remember pace is seconds/km, so a blue cell against a recovery metric "
            "means better recovery → faster running.")
    order = [c for c in LB if feat[c].notna().sum() >= 4]
    if len(order) >= 3:
        show(corr_heatmap(feat, order, LB), 460)
        # honest sample-size note: smallest pairwise overlap actually used
        cnts = feat[order].notna().sum()
        pace_n = int(feat["pace"].notna().sum()) if "pace" in order else 0
        st.caption(f"n per signal ≈ {int(cnts.min())}–{int(cnts.max())} days"
                   + (f" · run-pace pairs limited to {pace_n} runs" if pace_n else "")
                   + ". Small samples — read as exploratory, not proof. Correlation ≠ causation.")
    else:
        st.info("Not enough overlapping daily data yet to build a correlation matrix.")

    section("Explore a pair", "Recovery signal vs a run's pace",
            "Pick a recovery signal to plot against pace. A downward trend = better "
            "recovery lines up with faster running.")
    df = feat.dropna(subset=["pace"]).copy()
    hr_day = q("SELECT date, MIN(avg_hr) avg_hr FROM activities "
               "WHERE type LIKE '%running%' AND avg_pace_s_per_km IS NOT NULL GROUP BY date")
    df = df.merge(hr_day, on="date", how="left")
    xopts = [c for c in ["sleep_h", "sleep_score", "hrv", "stress", "rhr", "bb_drained"]
             if df[c].notna().sum() >= 4]
    if len(df) > 3 and xopts:
        xvar = st.selectbox("Compare pace against", xopts, format_func=lambda k: LB[k])
        sub = df.dropna(subset=[xvar, "pace"])
        if len(sub) > 3:
            try:
                import statsmodels  # noqa: F401
                trend = "ols"
            except ImportError:
                trend = None  # statsmodels optional; scatter still renders
            fig = px.scatter(sub, x=xvar, y="pace", color="avg_hr", trendline=trend,
                             color_continuous_scale=HR_SCALE,
                             labels={xvar: LB[xvar], "avg_hr": "avg HR"})
            fig.update_traces(selector=dict(mode="markers"),
                              marker=dict(size=12, opacity=0.85,
                                          line=dict(width=1, color=PLANE)))
            if trend:  # style the OLS line in the signature accent
                fig.update_traces(selector=dict(mode="lines"),
                                  line=dict(color=ACCENT, width=2.4, dash="dot"))
            fig.update_yaxes(autorange="reversed", title="run pace (s/km)")
            show(fig)
            corr = sub[xvar].corr(sub["pace"])
            faster = corr < 0
            st.caption(f"Pearson r = {corr:+.2f} across {len(sub)} runs — "
                       f"{'faster running with higher ' + LB[xvar].lower() if faster else 'weak / opposite relationship'}. "
                       "Correlation ≠ causation — confounders like hard days exist.")
    else:
        st.info("Need more runs joined with recovery data to explore pairs.")

# ============================================================ AI COACH
with tabs[7]:
    section("Ask your coach", "Claude · reading this same database",
            "Runs the Claude Code CLI already installed on this machine, so "
            "coaching rides the Claude subscription you already pay for — no "
            "API key lives in this project. The coach gets a briefing of your "
            "current state plus read-only query access to the same SQLite file "
            "these charts are drawn from.")

    # Probing shells out, and every Streamlit rerun re-executes this tab, so
    # cache it — a missing CLI is not going to appear mid-session.
    @st.cache_data(ttl=3600, show_spinner=False)
    def _coach_probe():
        return claude_cli.probe()

    @st.cache_data(ttl=300, show_spinner=False)
    def _coach_briefing() -> str:
        return coach.build_briefing()

    cli_ok, cli_msg = _coach_probe()

    st.session_state.setdefault("coach_msgs", [])
    st.session_state.setdefault("coach_sid", None)
    st.session_state.setdefault("coach_queued", None)

    if not cli_ok:
        st.warning(f"**AI coach unavailable.** {cli_msg}", icon="⚠")
        st.caption("Everything else in this dashboard works without it — the "
                   "charts read the database directly and never need Claude.")
    else:
        head_l, head_r = st.columns([5, 1])
        head_l.caption(f"Claude Code {cli_msg} · model set by your CLI default · "
                       f"reading `{config.DB_PATH.name}`")
        if head_r.button("New chat", use_container_width=True,
                         disabled=not st.session_state.coach_msgs):
            st.session_state.coach_msgs = []
            st.session_state.coach_sid = None
            st.rerun()

        # Starting points, so the tab is useful without having to think up a
        # question. Clicking one queues it through the same path as typing.
        if not st.session_state.coach_msgs:
            st.caption("Start with one of these, or ask anything below.")
            for row in (coach.SUGGESTED[:3], coach.SUGGESTED[3:]):
                for col, (label, full) in zip(st.columns(len(row)), row):
                    if col.button(label, use_container_width=True, key=f"sug_{label}"):
                        st.session_state.coach_queued = full
                        st.rerun()

            with st.expander("What the coach can see"):
                st.caption("Sent as a briefing on the first message of a chat. "
                           "Everything else it looks up itself, read-only.")
                st.code(_coach_briefing(), language="text")

        for msg in st.session_state.coach_msgs:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("meta"):
                    st.caption(msg["meta"])

        typed = st.chat_input("Ask about your training…")
        question = typed or st.session_state.coach_queued
        st.session_state.coach_queued = None

        if question:
            st.session_state.coach_msgs.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                status = st.empty()
                status.caption("Reading your training data…")
                holder: dict = {}

                def _turn():
                    """Yield text deltas; report tool calls through `status`."""
                    # The briefing only rides the first message — after that the
                    # resumed session already has it, and resending would bloat
                    # every turn and break the prompt cache.
                    first = st.session_state.coach_sid is None
                    prompt = coach.build_prompt(
                        question, briefing=_coach_briefing() if first else None,
                        include_briefing=first)
                    for kind, payload in claude_cli.stream_turn(
                            prompt,
                            system_prompt=coach.SYSTEM_PROMPT,
                            session_id=st.session_state.coach_sid,
                            use_tools=True):
                        if kind == "text":
                            yield payload
                        elif kind == "tool":
                            status.caption(f"Looking up `{payload}`…")
                        elif kind == "done":
                            holder["reply"] = payload

                try:
                    st.write_stream(_turn())
                except Exception as exc:  # a broken CLI must not kill the tab
                    holder.setdefault(
                        "reply",
                        claude_cli.Reply(is_error=True,
                                         error=f"{type(exc).__name__}: {exc}"))

                status.empty()
                reply = holder.get("reply") or claude_cli.Reply(
                    is_error=True, error="No response from Claude.")

                if reply.is_error:
                    st.error(f"**Coach failed.** {reply.error}", icon="⚠")
                    st.session_state.coach_msgs.pop()  # don't strand the question
                else:
                    st.session_state.coach_sid = reply.session_id
                    bits = []
                    if reply.duration_ms:
                        bits.append(f"{reply.duration_ms / 1000:.0f}s")
                    tools = [t for t in reply.tools_used if t != "ToolSearch"]
                    if tools:
                        bits.append("looked up "
                                    + ", ".join(f"`{t}`" for t in dict.fromkeys(tools)))
                    if reply.mcp_connected is False:
                        bits.append("⚠ data tools offline — answered from the "
                                    "briefing only")
                    meta = " · ".join(bits)
                    if meta:
                        st.caption(meta)
                    st.session_state.coach_msgs.append(
                        {"role": "assistant", "content": reply.text, "meta": meta})

        st.caption("Coaching guidance from a model, not a medical professional. "
                   "It reads real numbers, but check anything that matters.")
