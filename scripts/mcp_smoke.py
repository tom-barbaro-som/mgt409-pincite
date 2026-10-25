"""Call the MCP server over stdio (via .mcp.json) with no model: proves every agent-facing tool works.

  python scripts/mcp_smoke.py      -> writes output/mcp_smoke.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastmcp import Client  # noqa: E402

from backend.mcp_client import load_mcp_config  # noqa: E402

CONCEPTS = [["first-of-class", "lead ship"], ["cost growth", "cost overrun"], ["frigate", "shipbuilding"]]


async def main() -> None:
    case = "smoke-test"
    log = {}
    async with Client(load_mcp_config()) as c:
        async def call(name, **args):
            res = (await c.call_tool(name, args)).data
            log[name] = res
            return res

        tools = sorted(t.name for t in await c.list_tools())
        log["tools"] = tools
        await call("create_case", case_id=case, request="smoke test: first-of-class frigate cost growth")
        found = await call("search_reports", case_id=case, concepts=CONCEPTS, limit=3)
        top = found["results"][0]["report_id"]
        await call("shortlist_reports", case_id=case, picks=[{"report_id": top, "why": "smoke"}])
        await call("open_report", case_id=case, report_id=top)
        pages = await call("rank_report_pages", case_id=case, report_id=top, concepts=CONCEPTS, limit=2)
        page = pages["pages"][0]["pdf_page"]
        text = await call("read_passages", case_id=case, report_id=top, pages=[page], focus_terms=["cost growth"])
        quote = " ".join(text["pages"][0]["untrusted_report_text"][0].split()[:30])
        f = await call("record_finding", case_id=case, report_id=top, pdf_page=page, quote=quote,
                       claim="Smoke-test claim with no numbers.", relevance=3)
        await call("set_finding_status", case_id=case, finding_id=f["finding_id"], status="verified", note="smoke")
        await call("compile_brief", case_id=case, headline="Smoke test brief.",
                   sections=[{"heading": "Smoke", "summary": "Smoke test.", "finding_ids": [f["finding_id"]]}])
        await call("get_case_status", case_id=case)
    out = ROOT / "output" / "mcp_smoke.json"
    out.write_text(json.dumps(log, indent=2, default=str))
    print(f"{len(tools)} tools: {', '.join(tools)}")
    print(f"top report {top}, page {page}, finding {f['finding_id']} verified; wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
