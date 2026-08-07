"""End-to-end MCP smoke test: launch the server over stdio and exercise tools."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    env = dict(os.environ)
    env["GARMIN_DB"] = str(Path("data/garmin_sample.db").resolve())
    env["PYTHONUTF8"] = "1"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "garmin_coach.mcp.server"], env=env
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            prompts = await s.list_prompts()
            print("PROMPTS:", [p.name for p in prompts.prompts])

            async def call(name, **kw):
                res = await s.call_tool(name, kw)
                txt = res.content[0].text if res.content else ""
                return txt

            print("\n-- get_training_load --")
            print(await call("get_training_load"))
            print("\n-- get_pace_at_hr(135,145,12) --")
            print(await call("get_pace_at_hr", hr_low=135, hr_high=145, months=12))
            print("\n-- get_recent_metrics(5) (first row) --")
            rows = json.loads(await call("get_recent_metrics", days=5))
            print(rows[0] if rows else "no rows")
            print("\n-- run_sql SELECT --")
            print(await call("run_sql", query="SELECT COUNT(*) n FROM activities"))
            print("\n-- run_sql WRITE (must be rejected) --")
            print(await call("run_sql", query="DELETE FROM activities"))
            print("\n-- list_activities (1 run) --")
            acts = json.loads(await call("list_activities", start="2000-01-01",
                                         end="2100-01-01", type="running", limit=1))
            print(acts[0] if acts else "none")
            if acts:
                aid = acts[0]["activity_id"]
                det = json.loads(await call("get_activity_detail",
                                            activity_id=aid, include_streams=True))
                print("\n-- get_activity_detail: splits=%d streams=%d --" % (
                    len(det.get("splits", [])), len(det.get("streams_downsampled", []))))
            print("\nALL MCP CALLS OK")


if __name__ == "__main__":
    asyncio.run(main())
