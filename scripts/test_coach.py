"""End-to-end check of the AI coach bridge.

Verifies, in order: the Claude Code CLI is installed and logged in, the briefing
builds off the local DB, and a real headless turn reaches the Garmin MCP tools
and comes back with grounded text.

    PYTHONUTF8=1 python scripts/test_coach.py
    PYTHONUTF8=1 python scripts/test_coach.py --offline   # skip the live turn

The live turn spends a little of the user's Claude subscription (no API key, no
per-token bill). `--offline` covers everything except that call.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from garmin_coach.ai import claude_cli, coach  # noqa: E402

QUESTION = ("Using the garmin MCP tools, report my most recent run's date, "
            "distance and average HR. One sentence.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="skip the live Claude turn")
    args = ap.parse_args()

    print(f"DB: {config.DB_PATH}")
    if not config.DB_PATH.exists():
        print(f"FAIL: no database at {config.DB_PATH}")
        return 1

    # 1 -- CLI present and runnable.
    exe = claude_cli.find_cli()
    print(f"claude binary: {exe}")
    if exe and Path(exe).suffix.lower() in (".cmd", ".bat"):
        print("  WARNING: running through a batch shim. Arguments containing "
              "newlines may be truncated by cmd.exe -- see CLAUDE.md.")
    ok, msg = claude_cli.probe()
    print(f"probe: {'OK' if ok else 'FAIL'} -- {msg}")
    if not ok:
        return 1

    # 2 -- briefing builds off the real store.
    t0 = time.time()
    briefing = coach.build_briefing()
    print(f"briefing: {len(briefing)} chars in {time.time() - t0:.2f}s "
          f"({len(briefing) // 4} tokens approx)")
    for marker in ("## Activities", "## Recovery"):
        if marker not in briefing:
            print(f"FAIL: briefing missing {marker!r}")
            return 1
    print("briefing sections: OK")

    if args.offline:
        print("\n--offline: skipping the live turn. OK")
        return 0

    # 3 -- a real turn, with tools.
    print(f"\nasking: {QUESTION}")
    t0 = time.time()
    reply = None
    for kind, payload in claude_cli.stream_turn(
            coach.build_prompt(QUESTION, briefing),
            system_prompt=coach.SYSTEM_PROMPT, use_tools=True):
        if kind == "tool":
            print(f"  [tool] {payload}")
        elif kind == "done":
            reply = payload

    assert reply is not None
    print(f"\nelapsed:     {time.time() - t0:.1f}s")
    print(f"session_id:  {reply.session_id}")
    print(f"mcp garmin:  {reply.mcp_connected}")
    print(f"tools used:  {reply.tools_used}")
    print(f"denied:      {reply.denied_tools}")
    print(f"list cost:   ${reply.list_cost_usd or 0:.4f} "
          f"(API list price; not billed on a subscription)")
    print(f"\n{reply.text}\n")

    if reply.is_error:
        print(f"FAIL: {reply.error}")
        return 1
    if reply.mcp_connected is False:
        # The exact silent failure this script exists to catch.
        print("FAIL: the garmin MCP server did not connect, so the coach "
              "answered from the briefing alone. See the batch-shim note in "
              "CLAUDE.md.")
        return 1
    if not any(t != "ToolSearch" for t in reply.tools_used):
        print("WARNING: no data tool was called -- the briefing may have been "
              "enough, but tool access went unverified.")

    print("ALL COACH CHECKS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
