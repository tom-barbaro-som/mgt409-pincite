# PinCite: page-cited answers from U.S. government reports (MGT 409 Final Project)

A team of AI agents that finds evidence in long official US government reports (CRS, GAO). Ask a question like *"Find me data on first-of-class ship cost overruns."* You get back a short **evidence brief**. Every point in it is a verbatim quote with its **PDF page number**, a link that opens the PDF at that page, and a **highlighted screenshot** of the passage, so you can check and cite it in seconds.

- **MCP server** (`mcp_server/`, FastMCP, 18 agent tools + 6 system-only tools) is the only layer that touches report data. It contains a zero-token **ranking engine** (catalog → pages → passages, with a newest-reports lane and a priority lane), deterministic quote verification, screenshot rendering, and the shared **case board**.
- **Agents** (`backend/`, PydanticAI on `gpt-6-luna` via Portkey). The four-agent team is Communication (manager), Report Search, Report Reader, and Presentation (fact-checker). There is also a **Solo researcher** for the fast path. In **Auto** mode (the default), a question moves through up to three steps:
  1. **Plan.** The Solo researcher names what matters most (e.g., the Columbia, Virginia, Ford, and DDG-51 programs for a shipbuilding-cost question). The ranking engine finds the best report for each, plus the newest reports.
  2. **Quick pass.** The same agent reads and quotes; code verifies every quote and number and takes the screenshots.
  3. **Full team.** Only if fewer than 2 facts verify.

  **Quick** runs steps 1–2 only, and **Full team** runs step 3 only.
- **Dashboard** (`backend/main.py` FastAPI + `frontend/` React/Vite): ask a question, follow the workflow tracker (Plan → Quick pass → Full team only if needed) and a plain-English activity feed, then review the answer as evidence cards with highlighted page screenshots, open-at-page PDF links, and copy-ready citations.
- **Audit trail**: every model step, tool call, and delegation is appended to `output/audit_trail.json` (JSON Lines).

**Repository:** https://github.com/tom-barbaro-som/mgt409-pincite · Final report: [`report.html`](report.html) · Slides: [`slides.html`](slides.html)

Design details, token strategy, and safety are covered in [`docs/design.md`](docs/design.md).

## 1. Setup (once)

You need Python 3.12+ and a Portkey API key for the course. Get the code, then run these commands from the repository folder:

```bash
git clone https://github.com/tom-barbaro-som/mgt409-pincite.git
cd mgt409-pincite
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set PORTKEY_API_KEY in .env
```

The app looks for `.env` in this folder first, then one folder up. The key is never printed or logged. Only `gpt-6-luna` is allowed.

## 2. Start the dashboard (one command)

```bash
uvicorn backend.main:app --port 8010         # venv active, from this folder
```

Open **http://localhost:8010**. The built React app is committed in `frontend/dist/`, so **Node is not required**. FastAPI serves the app, the API (`/api/...`, docs at `/docs`), screenshots, and briefs.

1. **Ask.** Type a question in plain English (or click one of the examples) and pick a mode: **Auto** (recommended), **Quick**, or **Full team**. Under the mode picker, **What happens when you ask** draws that mode's workflow. Then click **Find evidence** or press Enter.
2. **Watch.** The workflow tracker shows which step is running ("Step 2 of 3: Quick pass"):
   - *Plan*, with the priorities it picked;
   - *Quick pass*, with how many facts code verified;
   - *Full team*, only if fewer than 2 facts verify.

   It has a running clock and a plain-English line for the latest action. Open **See what each team member is doing** for each agent's status and the recent steps.
3. **Read and check.** When it finishes, **How this answer was made** shows the path taken, and the **Answer & evidence** tab shows:
   - a status in plain words (*Answered*, *Partly answered*, *We couldn't find evidence for this*, or *Can you say a bit more?*);
   - the answer;
   - one evidence card per verified fact. Each card has the claim, the verbatim quote, the report and page, a date badge, and buttons for **View highlighted page**, **Open PDF at page N**, and **Copy quote + citation**.

   If a quick-pass answer comes back partial or empty, **Dig deeper with the full team** reruns the question with all four agents. **⬇ Printable report** opens a self-contained HTML version.
4. **Behind the scenes** shows:
   - how much work the question took (searches, reports opened, and pages read, against the hard limits);
   - the **research plan** (each priority and the report found for it, plus the newest reports);
   - which reports were considered (tagged *Priority* or *Newest*) and whether each was read;
   - AI calls and tokens per team member;
   - the full activity log.

Your latest 30 questions are listed under **Your questions** on the left, and each answer has its own link (`http://localhost:8010/#case-…`) to bookmark or share. **How it works** (top right) explains the tool to first-time users. The status badge reads **Ready**, **Working on a question**, or **API key missing** (check `.env`).

One question runs at a time. Typical times on the sample dataset (final October 25, 2026 test runs, medians):

| Mode | Typical time |
|---|---|
| Quick | about 20 s (13–32 s) |
| Auto | about 20 s; usually 1–1½ min when it hands off to the full team |
| Full team | about 50 s; up to about 2 min |

**Frontend development** (optional, needs Node 20+): run the backend as above, then

```bash
cd frontend && npm install && npm run dev     # http://localhost:5180 (proxies /api and /files to :8010)
npm run build                                 # rebuild frontend/dist after UI changes
```

**Command line** (no dashboard):

```bash
python -m backend.run_research "Please find me data regarding first-of-class ship cost overruns"          # Auto mode
python -m backend.run_research --mode lean "..."    # Quick (the solo pass only)
python -m backend.run_research --mode team "..."    # Full team only
```

This prints the reply, the brief path (`output/briefs/<case>.html`), and tokens per agent. It also saves `output/runs/<case>.json`.

Prompt caching uses explicit cache breakpoints (`CACHE_LAYOUT=per_agent`, the default). `CACHE_LAYOUT=shared` (one team handbook for every agent) and `off` are available for experiments; see `output/evals/cache_experiments.md`.

## 3. Datasets

| Dataset | What's in it | How to use |
|---|---|---|
| `sample` (default) | 10,731 reports in total: CRS reports in seven policy domains (defense, health, budget/tax, energy/environment, education/labor, immigration, infrastructure), plus 800 unrelated recent CRS reports as distractors and the 25 newest GAO reports from GAO's RSS feed. 99% have an abstract, and CRS revision counts are included. 4 CRS shipbuilding PDFs are cached for offline demos; other PDFs download on demand from EveryCRSReport. | nothing to do |
| `full` | The entire CRS catalog (23,567 reports in EveryCRSReport's index, October 2026) plus the GAO RSS feed. | `python scripts/build_index.py --dataset full --download`, then set `DATASET=full` in `.env`. Optional: `python scripts/enrich_catalog.py --all` and `--all --html` to crawl abstracts first (~15–30 min, one time). |

**Abstracts and recency.** EveryCRSReport's index has titles only. `scripts/enrich_catalog.py` crawls each report's metadata once, offline and at zero model cost. That gives abstracts for older reports and revision counts (a sign of a canonical, maintained report). Its `--html` pass reads summaries from the top of each newer report's HTML version. Without that pass, older reports, which have abstracts, would systematically outrank newer ones. **What matters most.** The planning agent names 1–4 priorities (the programs, laws, or updates with the largest cost or stakes). A separate priority lane finds the report whose title best names each one, preferring the most-revised (CRS updates its reports on major programs most) and the most recent. Two slots stay with the newest relevant reports. **Newest first.** Ranking puts the newest information first: recency is weighted 0.20; an older report is penalized when a newer one in the same series covers the same ground; a "current coverage" slot is reserved when every top result is more than 3 years old; and within a report, pages with its latest data rank higher. Briefs list the newest evidence first and add a freshness caveat when every source is more than 3 years old.

**Adding your own PDFs (e.g. GAO reports):** put the PDF in `data/library/` (create the folder if it isn't there; for example `data/library/gao-24-106546.pdf`) and rebuild with `python scripts/build_index.py --rebuild`. File names that contain a GAO number are recognized automatically. Use the optional `data/library/library.csv` (`file,report_id,source,title,pub_date,landing_url`) to add titles and dates. GAO blocks automated downloads, so the app never fetches GAO PDFs itself.

The index is built automatically on first use (`data/index_<dataset>.db`, git-ignored). To start fresh, run `python scripts/build_index.py --rebuild`. That clears case history. The audit trail is append-only and is never cleared.

## 4. Other commands

```bash
python scripts/mcp_smoke.py          # calls the MCP tools over stdio with no model -> output/mcp_smoke.json
python scripts/eval_suite.py [--mode auto|lean|team]   # 10-question multi-domain evaluation with the real agents -> output/evals/ (see docs/evals.md)
python scripts/baseline_single_agent.py   # cost baseline: one unoptimized agent on the same questions
python scripts/safety_tests.py [--live]   # red-team the defenses -> output/safety/safety_report.html
python scripts/build_report.py       # rebuild report.html (the final report) from the saved run data
python scripts/build_slides.py       # rebuild slides.html (reveal.js deck) from the same numbers
python scripts/capture_screenshots.py "question"   # optional: pip install playwright; captures docs/screenshots/
python -m mcp_server.server          # run the MCP server by itself (stdio)
```

In **Claude Code**, open this folder and approve the `gov-report-research` server from `.mcp.json` to use the tools directly.

## Layout

```
mgt409-pincite/
├── report.html              final report (self-contained; open in any browser)
├── slides.html              10-slide reveal.js deck (self-contained; reveal.js loads from a CDN)
├── AI_prompts.md            full prompt/response log
├── README.md · requirements.txt · .env.example · .mcp.json
├── docs/design.md           architecture, ranking, tokenomics plan, safety, Tier 2 ideas
├── docs/evals.md            multi-domain evaluation results (Oct 21 runs + Oct 25 update) and known misses
├── mcp_server/              server.py (24 tools) · catalog.py (ranking) · pdfs.py (pages, quotes, screenshots)
│                            store.py (SQLite + FTS5) · brief.py (HTML/Markdown brief)
├── backend/                 main.py (FastAPI dashboard API) · team.py (agent loop + delegation) · agents/ · prompts/
│                            events.py (feed summaries) · mcp_client.py (role-filtered tools) · run_research.py (CLI)
├── frontend/                React + Vite + TypeScript dashboard (src/) and its committed build (dist/)
├── scripts/                 build_index.py · enrich_catalog.py (abstract crawl) · eval_suite.py · mcp_smoke.py
│                            baseline_single_agent.py · safety_tests.py · build_report.py · build_slides.py
├── data/sample/             catalog.csv + pdfs/ (committed test dataset)
├── data/library/            your own PDFs
└── output/                  audit_trail.json · runs/ · evals/ · safety/ (measurements behind the report)
                             briefs/ · evidence/ are written per question and git-ignored
```

## Safety, in brief

- **No invented quotes.** A finding is refused unless its quote matches the PDF page text. It can't be verified if its claim contains numbers that aren't in the quote. The brief and the final reply are rejected if they state numbers not backed by a verified quote. When the quick pass gives the answer (Quick mode, and Auto unless it hands off to the team), the reply itself is written by code from verified facts only. If a reply still states an unbacked number after the allowed retries, code deletes that sentence rather than failing the run.
- **Report text is untrusted.** It's labeled as data, and instruction-like text is flagged. Downloads come only from allowlisted hosts, with a size limit and a PDF signature check.
- **Least privilege.** Each agent sees only its role's tools. The backend stamps the case id and the agent's identity on every call.
- **Spending limits.** Each case has a token, request, and tool-call budget, plus hard MCP limits: 6 searches, 4 reports opened, 16 pages read, and 12 findings. Limits are reserved atomically, so parallel tool calls can't race past them, and a report can be read or quoted only after the current question opened it. Delegation follows a directed graph with depth, count, and per-pair caps.
