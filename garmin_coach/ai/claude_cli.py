"""Transport: drive the local Claude Code CLI (`claude -p`) in headless mode.

Why the CLI and not the Anthropic API: `claude` authenticates with the user's
existing Claude subscription, so coaching rides a plan they already pay for and
this project never has to hold an API key. The cost of that choice is a hard
dependency on the CLI being installed and logged in -- `probe()` reports that
up front so the dashboard can degrade gracefully instead of erroring mid-answer.

Everything here is deliberately read-only:

* the prompt goes in over **stdin**, never argv -- on Windows `claude` is a
  `.cmd` shim, and shell metacharacters in a free-text question would otherwise
  need escaping (see BatBadBut/CVE-2024-24576).
* only the `mcp__garmin__*` tools are allow-listed, and that MCP server already
  opens the DB `mode=ro` and refuses anything but a single SELECT/WITH. No Bash,
  no Edit, no Write. In `-p` mode a non-allow-listed tool is denied, not
  prompted, so an unattended run can never block or mutate anything.
* the subprocess runs in a scratch cwd, so the repo's CLAUDE.md and hooks (which
  describe a *coding* agent) never leak into the running coach's context.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

# The MCP server's full read-only tool surface. Enumerated rather than passed as
# a wildcard so adding a tool to the server is a deliberate act here too.
GARMIN_TOOLS = (
    "get_schema", "run_sql", "get_recent_metrics", "list_activities",
    "get_activity_detail", "get_training_load", "get_pace_at_hr",
    "get_training_plan", "get_hr_zones", "get_fitness_snapshot",
    "get_strength_progress",
)
ALLOWED_TOOLS = tuple(f"mcp__garmin__{t}" for t in GARMIN_TOOLS)

DEFAULT_TIMEOUT_S = 240


class CoachUnavailable(RuntimeError):
    """The `claude` CLI is missing, or present but not usable."""


@dataclass
class Reply:
    """The outcome of one headless turn."""

    text: str = ""
    session_id: Optional[str] = None
    is_error: bool = False
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    tools_used: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    # Whether the Garmin MCP server actually came up. None until `init` is seen.
    # Worth surfacing: when it silently fails the coach still answers, just
    # without the ability to look anything up beyond the briefing.
    mcp_connected: Optional[bool] = None
    # What the same turn would have cost on API list pricing. On a subscription
    # nothing is billed per-token -- shown only so heavy use stays visible.
    list_cost_usd: Optional[float] = None


# --- discovery ---------------------------------------------------------------

def _unwrap_npm_shim(shim: str) -> str:
    """Resolve npm's `claude.cmd` to the real `claude.exe` it wraps.

    This matters more than it looks. The shim is a batch file, so Windows runs
    it through cmd.exe, which re-parses the command line. An argument containing
    a newline -- our multi-line system prompt -- terminates that line, and every
    flag after it is silently dropped. Observed effect: `--mcp-config` and
    `--strict-mcp-config` disappear, the Garmin server never loads, and the
    user's unrelated global MCP servers get pulled in instead. No error is
    raised; the coach just quietly answers with no data.

    Going straight to the .exe skips cmd.exe entirely, so arguments reach the
    process verbatim.
    """
    p = Path(shim)
    if p.suffix.lower() not in (".cmd", ".bat"):
        return shim
    exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if exe.exists() else shim


def find_cli() -> Optional[str]:
    """Absolute path to the `claude` executable, or None.

    Honours GARMIN_CLAUDE_BIN for non-standard installs. On Windows, resolve
    through to the real .exe where possible -- see `_unwrap_npm_shim`.
    """
    override = os.getenv("GARMIN_CLAUDE_BIN")
    if override and Path(override).exists():
        return _unwrap_npm_shim(override)

    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            found = shutil.which(f"claude{ext}")
            if found:
                return _unwrap_npm_shim(found)
    found = shutil.which("claude")
    return _unwrap_npm_shim(found) if found else None


def probe() -> tuple[bool, str]:
    """Check the CLI is installed and responding. Returns (ok, message)."""
    exe = find_cli()
    if not exe:
        return False, (
            "Claude Code CLI not found on PATH. Install it with "
            "`npm install -g @anthropic-ai/claude-code`, then run `claude` once "
            "to log in. Set GARMIN_CLAUDE_BIN to override the path."
        )
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=60,
            cwd=str(_scratch_dir()),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"Found {exe} but could not run it: {exc}"
    if out.returncode != 0:
        return False, f"`claude --version` exited {out.returncode}: {out.stderr.strip()[:200]}"
    return True, out.stdout.strip()


# --- wiring ------------------------------------------------------------------

def _scratch_dir() -> Path:
    """Neutral cwd for the subprocess, so no repo CLAUDE.md or hooks load."""
    d = config.DATA_DIR / "coach_workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_mcp_config(path: Optional[Path] = None) -> Path:
    """Write the MCP server definition the CLI will launch, return its path.

    Regenerated on every run: `sys.executable` and the DB path are whatever this
    Streamlit process is actually using, so the coach can never end up reading a
    different database than the charts above it.
    """
    server = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
    cfg = {
        "mcpServers": {
            "garmin": {
                "command": sys.executable,
                "args": [str(server)],
                "env": {"GARMIN_DB": str(config.DB_PATH)},
            }
        }
    }
    path = path or (_scratch_dir() / "mcp.json")
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def _build_command(
    exe: str,
    *,
    system_prompt: str,
    mcp_config: Path,
    session_id: Optional[str],
    model: Optional[str],
    stream: bool,
    use_tools: bool,
) -> list[str]:
    cmd = [exe, "-p", "--output-format", "stream-json" if stream else "json"]
    if stream:
        # stream-json requires --verbose; partial messages give us text deltas.
        cmd += ["--verbose", "--include-partial-messages"]
    cmd += ["--system-prompt", system_prompt]
    if use_tools:
        cmd += ["--mcp-config", str(mcp_config), "--strict-mcp-config",
                "--allowed-tools", *ALLOWED_TOOLS]
    else:
        # No data tools: the briefing in the prompt is the only source.
        cmd += ["--strict-mcp-config"]
    if session_id:
        cmd += ["--resume", session_id]
    if model:
        cmd += ["--model", model]

    # Safety net for installs where the .exe could not be resolved and we are
    # still going through a batch shim: collapse newlines so cmd.exe cannot
    # truncate the command line mid-argument (see `_unwrap_npm_shim`).
    if Path(exe).suffix.lower() in (".cmd", ".bat"):
        cmd = [" ".join(a.split()) if isinstance(a, str) and "\n" in a else a
               for a in cmd]
    return cmd


# --- execution ---------------------------------------------------------------

def stream_turn(
    prompt: str,
    *,
    system_prompt: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    use_tools: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Iterator[tuple[str, object]]:
    """Run one turn, yielding ``(kind, payload)`` as it happens.

    Kinds: ``text`` (str delta), ``tool`` (str tool name), ``done`` (Reply).
    A ``done`` is always yielded last, even on failure, so callers can render a
    single terminal state without special-casing exceptions.
    """
    exe = find_cli()
    if not exe:
        ok, msg = probe()
        yield "done", Reply(is_error=True, error=msg)
        return

    mcp_config = write_mcp_config()
    cmd = _build_command(
        exe, system_prompt=system_prompt, mcp_config=mcp_config,
        session_id=session_id, model=model, stream=True, use_tools=use_tools,
    )

    reply = Reply()
    started = time.monotonic()
    proc = None
    # stderr goes to a file, not a pipe: nothing drains a stderr pipe while we
    # block reading stdout, so a chatty run could fill the buffer and deadlock.
    err_path = _scratch_dir() / "claude_stderr.log"
    try:
        with open(err_path, "w+", encoding="utf-8", errors="replace") as errf:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=str(_scratch_dir()),
                env={**os.environ, "GARMIN_DB": str(config.DB_PATH), "PYTHONUTF8": "1"},
            )
            # Prompt over stdin, never argv -- no shell-quoting surface.
            proc.stdin.write(prompt)
            proc.stdin.close()

            for line in proc.stdout:
                if time.monotonic() - started > timeout_s:
                    proc.kill()
                    reply.is_error = True
                    reply.error = f"Timed out after {timeout_s}s."
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # non-JSON chatter on stdout is not fatal
                for item in _handle_event(event, reply):
                    yield item
    except OSError as exc:
        reply.is_error = True
        reply.error = f"Could not launch {exe}: {exc}"
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if proc.returncode not in (0, None) and not reply.text:
                try:
                    stderr = err_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    stderr = ""
                reply.is_error = True
                reply.error = (reply.error
                               or f"claude exited {proc.returncode}: {stderr.strip()[-400:]}")

    if not reply.text and not reply.is_error:
        reply.is_error = True
        reply.error = "Claude returned no text."
    yield "done", reply


def _handle_event(event: dict, reply: Reply) -> Iterable[tuple[str, object]]:
    """Fold one stream-json event into `reply`, emitting anything renderable."""
    etype = event.get("type")

    if etype == "stream_event":
        inner = event.get("event", {})
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    reply.text += text
                    yield "text", text
        return

    if etype == "system" and event.get("subtype") == "init":
        reply.session_id = event.get("session_id") or reply.session_id
        servers = event.get("mcp_servers")
        if servers is not None:
            reply.mcp_connected = any(
                s.get("name") == "garmin" and s.get("status") == "connected"
                for s in servers
            )
        return

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []) or []:
            if block.get("type") == "tool_use":
                name = str(block.get("name", "")).replace("mcp__garmin__", "")
                if name:
                    reply.tools_used.append(name)
                    yield "tool", name
        return

    if etype == "result":
        reply.session_id = event.get("session_id") or reply.session_id
        reply.duration_ms = event.get("duration_ms")
        reply.num_turns = event.get("num_turns")
        reply.list_cost_usd = event.get("total_cost_usd")
        reply.denied_tools = [
            str(d.get("tool_name", "")) for d in event.get("permission_denials", []) or []
        ]
        # `result` carries the authoritative final text; deltas can miss a tail.
        final = event.get("result")
        if isinstance(final, str) and final.strip():
            reply.text = final
        if event.get("is_error"):
            reply.is_error = True
            reply.error = event.get("api_error_status") or reply.text or "Claude reported an error."
        return


def ask(prompt: str, **kwargs) -> Reply:
    """Blocking convenience wrapper around `stream_turn`."""
    reply = Reply()
    for kind, payload in stream_turn(prompt, **kwargs):
        if kind == "done":
            reply = payload  # type: ignore[assignment]
    return reply
