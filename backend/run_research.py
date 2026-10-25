"""Run one research request from the command line.

  python -m backend.run_research "Find data on first-of-class ship cost overruns"

Prints the team's reply, the brief path, and token usage per agent. Every step is
appended to output/audit_trail.json; the run summary is saved to output/runs/.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import PROJECT_DIR
from .team import ResearchTeam, save_run


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("request", help="What you want the team to find")
    ap.add_argument("--mode", choices=["auto", "lean", "team"], default="auto",
                    help="auto (default): lean first, full team only if needed")
    args = ap.parse_args()

    async with ResearchTeam() as team:
        result = await team.run_case(args.request, mode=args.mode)

    out = save_run(result)

    print(f"\n=== {result.case_id} · {result.outcome} · {result.mode} mode"
          f"{' → escalated to team' if result.escalated else ''} · {result.seconds}s ===")
    if result.reply:
        print(f"[{result.reply.status}]\n{result.reply.reply}")
        if result.reply.clarifying_question:
            print(f"\nQuestion for you: {result.reply.clarifying_question}")
        if result.reply.brief_html:
            print(f"\nBrief: {Path(PROJECT_DIR / result.reply.brief_html)}")
    else:
        print(result.message)
    u = result.usage
    print(f"\nTokens: input {u.input_tokens:,} (cached {u.cache_read_tokens:,}, cache writes {u.cache_write_tokens:,}) · output {u.output_tokens:,} · "
          f"requests {u.requests} · delegations {result.delegations}")
    for name, a in result.usage_by_agent.items():
        print(f"  {name:<14} in {a.input_tokens:>7,} (cached {a.cache_read_tokens:>7,}) out {a.output_tokens:>6,} "
              f"requests {a.requests}")
    print(f"Run summary: {out.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    asyncio.run(main())
