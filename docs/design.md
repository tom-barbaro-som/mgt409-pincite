# Design: Gov Report Research Team

## Problem

Analysts, congressional staff, journalists, students, and contractors need specific, citable facts from CRS and GAO reports. These reports often run 40 to 150 pages, and the relevant ones are scattered across more than 23,000 CRS products and thousands of GAO products. Today people keyword-search agency sites, skim PDFs, and copy figures by hand. A general chatbot either can't read the PDFs or summarizes them without page-level citations you can trust.

## Core idea: rank before you read

The team should **never brute-force**. A Google-style ranking stack does the narrowing deterministically and costs zero model tokens. The model reads only the best ~700-character passages on the best few pages of the best few reports.

```
 23,500+ reports ──▶ [1] catalog ranking ──▶ 1-3 reports ──▶ [2] page ranking ──▶ 2-4 pages ──▶ [3] passage selection ──▶ ~700-char passages
   (metadata)          BM25 + concepts          shortlist        BM25 + data density      snippets         focus-term windows            LLM reads only this
```

| Stage | Signals (all in `mcp_server/`) | Model tokens |
|---|---|---|
| **1. Catalog** (`catalog.rank_reports`) | **Newest to oldest.** Reports are grouped into eras (current ≤3 yrs, recent 3–8, archive 8+). Candidates are recalled **separately in each era**, so older reports with only a title still compete. Results come from the newest era with *strong* matches (topical fit ≥80% of the best in any era). Older eras fill in only once newer strong matches are used up; searching again continues down the eras. Within an era, the score is FTS5 **BM25** on title (×8) and abstract (×2), porter-stemmed (word-level recall plus an exact-phrase bonus), plus **IDF-weighted concept coverage**. Title matches count 1.0 and abstract-only matches 0.65. Then **depth**, **recency** (0.20, 4-year decay), **authority** (revision count, citation in-links, past verified findings), a **superseded** penalty when a newer report in the same series covers the same ground, and **MMR diversity**. Each result returns `why_ranked`, its date and era, and `newest_to_oldest` (strong matches left per era). | results only (5 compact rows) |
| **2. Pages** (`pdfs.rank_pages`) | BM25 per page, concept coverage, **data density** (dollar amounts, percents, figures), **freshness** (pages citing the report's latest years), a prior for summary and "What GAO Found" sections, and a **boilerplate penalty** (TOC, disclaimers, author pages). Returns ~300-char snippets. | snippets only |
| **3. Passages** (`pdfs.passages`) | Sentence-aligned ~700-char windows scored by focus terms and numbers. The best 2 per page. | ~1.4k chars per page instead of ~4k |

**Priority lane: what matters most, next to what is newest (added 2026-10-25).** Ranking newest-first alone over-rotated on new reports. On "first-of-class ship cost overruns", the newest reports on small programs (the TAO-205 oiler, the LPD-17/LHA amphibious ships, the FFG-62 frigate) took the slots. Every saved run before this change cited only one of the four flagship-program reports (the Ford-class carrier). Now the first agent writes a short **research plan**: `search_reports(priorities=[...])` with the 1–4 programs, laws, or updates of largest cost or stakes, most important first (e.g. "Columbia-class submarine SSBN-826", "Virginia-class submarine SSN-774", "Ford-class aircraft carrier CVN-78", "DDG-51 destroyer").
- `catalog.rank_priorities` matches each priority to a report by the IDF-weighted share of its words in the report title (an abstract-only match counts 0.65, minimum fit 0.5). Only reports within 0.1 of the best fit compete, so a full title match beats a partial one.
- Among those, it scores authority (revision count: CRS keeps its major-program reports current, with 137–204 revisions for the carrier, submarine, destroyer, and shipbuilding-plan reports vs 31–71 for small programs), recency, and topic coverage. Each priority gets a different report.
- A hand-added library PDF can't take a priority slot from a catalog report that also fits. The red team's planted "CRS report", titled to match a likely priority, took the slot before this rule.
- The newest-to-oldest lane keeps 2 slots. Each result is tagged `lane: newest` or `lane: priority: ...`.
- The plan is saved on the case board (`cases.plan`), the lane on each shortlist row (`shortlist.lane`), and the dashboard shows both.

**Crawled abstracts remove an old-report bias.** EveryCRSReport's index has titles only. A one-time offline crawl (`scripts/enrich_catalog.py`) adds metadata abstracts for older reports and reads summaries from the HTML versions of newer ones, covering 10,660 of the sample's 10,731 reports. Without the HTML pass, only pre-2018 reports had abstracts and they outranked newer reports. Briefs list the newest evidence first, every fact carries its report date, and `compile_brief` adds a freshness caveat when every source is more than 3 years old.

**The index learns at zero token cost.** When a report is opened, its own summary is written back into the catalog, and the reports it cites become in-links, so later searches rank better. Verified findings also boost a report for future cases. Page text is indexed once and reused across cases.

## The agents

| Agent | Job | Tools (role-filtered, default-deny) | May delegate to |
|---|---|---|---|
| **Communication** (manager) | Scopes the request and names the plan's priorities (or keeps the quick pass's), asks a clarifying question if it's too vague, delegates each step, decides whether to run one more round, and writes the reply. Never reads reports. | `get_case_status`, `get_brief`, `get_report_card` | search, reader, presentation |
| **Report Search** | Turns the request into concept and synonym groups, ranks the catalog with the plan's `priorities`, re-queries only when results match just generic concepts, and files the **best 1-3** reports (top priority first, then the newest). | `search_reports`, `get_report_card`, `shortlist_reports`, `get_shortlist` | — |
| **Report Reader** | Progressive deepening: outline → ranked pages → passages. Records **verbatim, page-cited findings** and stops once it has 3-6 strong ones. Asks Search for more, naming its gaps, if the shortlist is weak. | `get_shortlist`, `open_report`, `rank_report_pages`, `read_passages`, `record_finding`, `mark_report`, `get_report_card` | search (≤ 2×) |
| **Presentation** (fact-checker) | Independently re-checks each finding: quote match, numbers in claim vs. quote, and surrounding context. Verifies or rejects each one and compiles the brief. | `list_findings`, `check_finding`, `set_finding_status`, `compile_brief`, `get_report_card` | reader (1×, to fix a finding) |

```
            user
             │
     ┌───────▼────────┐   status/brief only
     │ Communication  │◀──────────────────────────┐
     └─┬──────┬─────┬─┘                           │
       │      │     │            CASE BOARD (MCP, SQLite)
       ▼      ▼     ▼        shortlist ─▶ findings ─▶ verified + brief
   Search  Reader  Presentation   ▲          ▲              ▲
       ▲      │  ▲     │          │          │              │
       └──────┘  └─────┘     (agents write/read by id, not by pasting text)
```

**Why a team reduces tokens.** Each agent's context holds only its own step. The manager sees status counts and the final brief, never page text. Search sees metadata, never PDFs. The Reader sees passages. Presentation sees one finding plus about 800 characters of context at a time. Work moves through the **case board by id**, so a 600-character quote is never re-sent through three agents. The static prompt comes first in each request and carries a per-agent `prompt_cache_key`, which raises cached-input hits.

**Why a directed graph instead of full connectivity.** In hw5 any agent could delegate to any other. Here the edges follow the pipeline, and each one has a per-case cap (`PAIR_LIMITS` in `backend/team.py`). That rules out ping-pong loops and keeps the cost of each case predictable.

## Workflow, modes, and prompt caching (added 2026-10-25)

Auto mode moves a question through up to three steps. The dashboard draws them on the home page, while a question runs, and above every answer:

```
 ① Plan ──────────▶ ② Quick pass ─────────────────▶ ≥2 facts verified? ──yes──▶ Answer
 Solo researcher:     same agent reads + quotes;            │
 priorities + newest  CODE verifies quotes and numbers,     no (or "Dig deeper")
                      screenshots, writes the reply         ▼
                                              ③ Full team: Coordinator → Researcher / Reader / AI Fact-checker
```

- **Quick:** steps ① and ② only. A single *Solo researcher* agent plans (priorities + newest), then uses at most 2 searches (enforced in code by `AGENT_CALL_CAPS` in `backend/mcp_client.py`), 3 reports, and 8 pages. It makes one `record_findings` call. Code then does the rest: `auto_verify` checks each quote against its page and each number in the claim against the quote. Verified findings get highlighted screenshots. The reply is written by code from verified findings only (`backend/team.py::_lean_reply`). If code reports a near-miss quote, the agent gets one retry.
- **Full team:** step ③ only, with the Coordinator doing the planning.
- **Auto (default):** ① → ② → ③ only if needed. If the quick pass verified fewer than 2 facts and either read a report or found on-topic reports (best concept coverage ≥ 0.4; off-topic questions score ≤ 0.2 and stop here, cheaply), the case limits are reset and the full team takes over (an `escalated` audit event) with the plan's priorities and the quick pass's gaps in its brief. A partial Quick answer also offers "Dig deeper with the full team" in the dashboard.
- **Last-retry repair:** if a reply or headline still states a number no verified quote contains after the allowed retries, code deletes just those sentences (`output_repaired`) instead of failing the run.
- **Caching:**
  - Each agent's fixed instructions are sent as a system message with an explicit cache breakpoint: `openai_cache_instructions`, a 30-minute TTL, and a per-agent `prompt_cache_key`.
  - Within a run, reuse is reliable: agents' first requests went from 0% to 87% cached.
  - Across runs, reuse is intermittent at low traffic.
  - A shared "team handbook" prefix (`CACHE_LAYOUT=shared`) gave no cross-agent hits, so `per_agent` is the default.
  - Details are in `output/evals/cache_experiments.md`.
- **Measured:** see the final report (`report.html`, Section 5) and `docs/evals.md`, both built from `output/evals/`.

## Dashboard

`backend/main.py` (FastAPI) runs the team as a background task, one request at a time (cost control). The React app (`frontend/`) polls `/api/events`, which reads the append-only audit trail and turns each entry into a one-line summary (`backend/events.py`). The live view is therefore exactly what was logged, and it survives a page reload. The workflow tracker (Plan → Quick pass → Full team → Answer, `frontend/src/components/Workflow.tsx`) is rebuilt from those events. It shows which step is live, the plan's priorities, the number of facts code verified, and whether the team was needed. Each team member's state (working, done, or standing by) is also shown. Above every finished answer, "How this answer was made" shows the path taken. Behind the scenes shows the research plan, and each considered report is tagged Priority or Newest. Each verified finding is a card with the quote, citation, highlighted screenshot, `Open PDF at page N` (the cached PDF served at `/api/reports/<id>/pdf#page=N`), and copy-to-clipboard. FastAPI also serves the built app from `frontend/dist`, so graders need only Python.

## Anti-hallucination gates (all deterministic, in code)

1. `record_finding` refuses a quote that matches its PDF page at less than 0.90. The match is alphanumeric-normalized, so line breaks, hyphenation, quote styles, and footnote markers glued to a sentence ("MFP.78 The") don't count against it. It also warns when the claim contains numbers that aren't in the quote.
2. `set_finding_status(verified)` requires a match of at least 0.97 and every claim number present in the quote. Only then does it render the highlighted screenshot.
3. `compile_brief` cites verified findings only. Any number in the headline or a section summary must appear in the cited quotes.
4. The Communication agent's final reply (and the Solo researcher's headline) runs through an output validator. A number that doesn't appear in any verified quote or citation triggers a `ModelRetry`, and the rejection is logged as `output_rejected`. After the last allowed retry, code removes the offending sentences (`output_repaired`).
5. Quotes, page numbers, links, and screenshots in the brief are filled in from the database, never retyped by a model.

## Safety

| Risk | Defense |
|---|---|
| Prompt injection inside a PDF | Report text is returned as `untrusted_report_text`, and instruction-like patterns are flagged with a warning. Shared team rules tell every agent to treat that text as data. Agents can't send anything anywhere: the only writes go to the case board. |
| Invented facts or wrong citations | Gates 1-5 above. A human can check each card against its screenshot and its `#page=N` link. |
| Wrong or unauthorized actions | Default-deny role tools. The backend stamps `case_id` and identity on every call, so an agent can't touch another case. `create_case`, `list_cases`, `catalog_info`, and `report_pdf_path` are system-only (dashboard backend). |
| Infinite loops and overspending | Each case has a token budget (250k), a request cap (45), and a tool-call cap (90). The MCP server enforces hard limits: 6 searches, 3 shortlist rounds, 4 reports opened, 16 pages read, 12 findings, each reserved atomically so parallel tool calls cannot race past it. A report's pages can be read or quoted only after this case opened it (pages indexed by another case don't bypass the report limit). Delegation has a depth cap (3), a count cap (8), and per-pair caps. |
| Malicious or huge downloads | PDFs come only from allowlisted hosts (EveryCRSReport, GovInfo), with no redirects, a 40 MB limit, and a `%PDF-` signature check. GAO PDFs are never auto-fetched. |
| Key leakage | The key loads from `.env` into the process environment only. It's never logged, `.env` is git-ignored, and only `gpt-6-luna` is allowed. |

## Data sources (and an honest limit)

- **CRS**: EveryCRSReport.com publishes a bulk index and PDFs for public reuse, with no key needed. Its index has titles and dates but no abstracts. Abstracts are learned when a report is opened.
- **GAO**: gao.gov's RSS feed works and gives the most recent products with summaries. GAO's site blocks automated requests (HTTP 403) for search, product pages, and PDFs. The app respects that block and doesn't scrape around it. GAO PDFs enter through `data/library/` (downloaded by a person). A hosted deployment could use GovInfo's API with an api.data.gov key.

## First measured run (2026-10-21, sample dataset, gpt-6-luna, reasoning effort low)

Request: *"Please find me data regarding first-of-class ship cost overruns"*. Case `case-20261021-160202-116a` finished as `partial` in 163 s, with 4 delegations. Search filed DDG-1000, LCS, and Ford CVN-78. The Reader asked Search for more once, which added the 2006 LCS report RS21305 (downloaded on demand). Presentation verified 4 findings and compiled the brief at `output/briefs/case-20261021-160202-116a.html`.

| Agent | Requests | Input tokens | Cached input | Output tokens |
|---|---|---|---|---|
| Communication | 7 | 22,511 | 11,344 | 1,251 |
| Report Search | 10 | 34,059 | 23,471 | 1,907 |
| Report Reader | 16 | 124,639 | 101,364 | 1,758 |
| Presentation | 5 | 19,138 | 13,158 | 1,091 |
| **Total** | **38** | **200,347** | **149,337 (75%)** | **6,007** |

**Second run (from the dashboard):** *"How has the Navy frigate (FFG-62) program changed in cost and schedule?"* Case `case-20261021-162239-f483` finished as `partial` in 84 s, with 3 delegations, 1 report opened, 7 pages read, and 4 verified findings. Totals: 31 requests, 136,329 input tokens (91,186 cached, 67%), and 4,285 output. Report Reader used 85,222 input tokens, 63% of the total. The pattern holds: when the topic maps to a single program report, the team opens just one report and stops early.

The Reader is 62% of input tokens because its context grows with every passage it reads. The next cost cut is a PydanticAI history processor that drops old passage text from the Reader's history once findings are recorded (they're on the case board anyway). The Reader's 16 requests should also be compared against a brute-force baseline (Tier 2 idea 1). Narrowing at work: the Reader opened 3 reports totaling 65 pages but read passages from only 10 of them. Search used all 6 of its allowed searches across two rounds, so the search cap was binding.

## Tier 2 differentiation ideas (ranked by value ÷ effort)

1. **Brute-force baseline experiment (tokenomics evidence).** Run the same 5-10 questions two ways: (a) a single agent given the full text of the top-3 PDFs, and (b) this team. Report tokens, cost, time, and accuracy (findings a human confirms as correct). This is the strongest "thoughtful agent design" evidence and fills the Tokenomics rubric row with measured numbers.
2. **Hallucination red-team test.** Use a scripted set of adversarial findings: a fabricated quote, a real quote with an inflated number, a quote taken out of context. Then add a planted PDF in `data/library/` containing "ignore previous instructions...". Show each one blocked, with screenshots of the refusals and the audit trail. This covers the "defense shown working" requirement.
3. **Corroboration and conflict detection.** When CRS and GAO give the same figure, mark it *corroborated*. When they differ (e.g. different cost-growth percentages for the same ship), show both side by side with dates. Analysts care about this, and chatbots don't do it.
4. **Table-to-CSV extraction.** For "find me data" requests, pull the cited table (`page.find_tables()`) into a downloadable CSV that carries the page citation.
5. **Copy-ready citations.** Export Chicago, Bluebook, or APA footnotes with pin cites, plus a Word or Google Doc export of the brief.
6. **Free follow-ups.** A follow-up question on the same case reuses the case board (findings already read) and opens new pages only if the answer is missing. Measure the near-zero marginal cost.
7. **Version-change alerts.** CRS reports are revised often. Compare a cited figure across report versions and flag when it changed, or let a user watch a topic.
8. **Real-user timing study (pushes toward Tier 3).** Time one or two people (a classmate, a defense analyst, a Hill staffer) finding three citable facts manually vs. with the app, and record their quotes.

Things to avoid: adding more LLM agents. Most of the "intelligence" that saves tokens is deterministic ranking. Another agent adds context and cost without new evidence.
