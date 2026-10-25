# Final Project: AI prompt log

Full record of the prompts used to build this project. User messages are copied verbatim, and assistant responses follow each one. Secrets are never recorded.

---

# 1. Project setup and agent team

## User prompt

```text
https://zlisto.github.io/mgt_409_fa26/project_requirements.html#repo

I have to make a final project for my AI class, linked above. Please use the Final Project folder for all work unless otherwise specified. First, I'd like you to set the project up by creating an AI_prompts.md file that records all of our work - copy over and continue to append all of my messages without modification. Include your responses in the file as well. I'd like you to use the root folder's working and safety conventions for agents and such. I also have instructions about using an API key to call gpt-6-luna for the agent in the app we're going to make.

Conceptually, I'd like to describe my project idea. I'd like to make a team of agents that would help someone find useful information from large official US government reports (e.g., GAO, CRS). There should be a team of agents - one that focuses on user communication, another that finds and prioritizes potentially relevant reports, another that actually reads the reports and pulls interesting findings. The reason for having a team of agents rather than just one is to reduce compute by limiting the contextual knowledge/work of each agent. We'll need to make some MCP tools so each agent can do its job and delegate to peer agents. The communication agent will handle all executive decision making like the team's manager. As the agents work, they'll need to share their actions in a file called audit_trail.json. If you're unsure about how I've set up teams of agents in the past, refer to the hw5 subfolder for useful reference files.

The team of agents would save humans searching and reading time, and I think the key differentiator from a basic chatbot would be how the information is served. I would suggest the information be screenshotted and cited with pdf page numbers so a human can choose what they want to site and quickly verify the data manually. Because running the agent team may be costly in tokens, due to the size and quantity of these reports, we need to be thoughtful in how we design the Report Search agent.

Intended agents (willing to add more or modify scope if you have good suggestions):

* Communication: takes user requests (e.g., "Please find me data regarding first-of-class ship cost overruns") and serves responses from the team. Also controls team delegation, execution, and judgement calls if needed.
* Report Search: finds relevant GAO, CRS, and similar official US government reports to the topic at hand. Provides links to the best reports to Report Reader. Report Search needs to be careful to only provide the best few results. If the results aren't good enough, Report Reader should ask for more. This will limit the token usage because the agents can work together iteratively to find the best results without brute force.
* Report Reader: Reads the provided links from Report Search and narrows focus the the most relevant and useful pieces of information. Cites the best findings and provides them to the Presentation agent. The findings should be select ranges of the reports so the Presentation agent doesn't need to read extended sections.
* Presentation: Compiles the findings in a human-readable format. There should be an emphasis on factual accuracy and no hallucinations. This agent is responsible for fact checking its peers.


Please start by setting up the project and agents. Also make suggestions for how this project can be differentiated like a Tier 2 project
```

## User prompt (sent while the assistant was working)

```text
It's important to me that the team of agents does NOT do their work through brute force. The agents should use creative search solutions to minimize churn (like how Google does a prioritized search engine ranking, but for this specific application).
```

## Assistant response

I set up the project in `Final Project/`, following the class folder's conventions and the hw5 team pattern: FastMCP server, PydanticAI agents on `gpt-6-luna` via Portkey, and an append-only JSON Lines `output/audit_trail.json`. I also created a local `.venv`, `requirements.txt`, `.env.example`, `.gitignore`, `.mcp.json`, `README.md`, `docs/design.md`, and a local git repo (nothing committed). The key is loaded from `.env` in the repo, then `.env` one folder up (the course rule), then the class `Portkey.env`. It is never printed.

**Ranking, not brute force (your second message).** The MCP server includes a zero-token, Google-style ranking stack. Stage 1, catalog: BM25 + IDF-weighted concept coverage + depth/recency/authority (citation in-links and past verified findings) + MMR diversity. Stage 2, pages: BM25 + data density + summary-section prior + boilerplate penalty. Stage 3: ~700-character passages. The LLM reads only stage-3 output. Each result explains `why_ranked`, and a `score_cliff` hint tells agents when more results won't help. The index also learns as it goes: opened reports contribute their summaries and citations.

**Agents.** Four agents, with a directed delegation graph and per-pair caps:
- Communication (manager): scopes the request, delegates, judges, replies.
- Report Search: concept and synonym queries, files the best 1-3 reports.
- Report Reader: outline → ranked pages → passages, then verbatim page-cited findings. Can ask Search for more up to twice.
- Presentation: independent fact-check, highlighted screenshots, cited HTML brief.

Agents share work through a case board (shortlist → findings → brief) by id, so report text is never passed between contexts.

**Anti-hallucination.** These checks are deterministic code:
- A quote must match its PDF page or the finding is refused.
- Every number in a claim must appear in its quote before the finding can be verified.
- The brief and the final reply are rejected if they contain unbacked numbers.

**Data.** The sample dataset has 4,035 catalog entries and 4 cached CRS shipbuilding PDFs. The full dataset is about 23.5k CRS reports from EveryCRSReport's public index plus GAO RSS. GAO blocks automated access (HTTP 403), and I didn't work around that. GAO PDFs go in `data/library/` by hand.

**Verified.** Syntax checks pass. An MCP stdio smoke test confirms all 16 tools work. A live run on the demo request completed in 163 s and produced a brief with 4 verified findings and highlighted screenshots: the first LCS estimate up about 29%, and the DDG-1000 class estimate up 54.4%. It used 200k input tokens (75% cached) and 6k output; the Reader accounted for 62%. The first live attempt exposed a SQLite threading bug and overly strict phrase search. Both are fixed, and that aborted run remains in the audit trail.

**Tier 2 suggestions** (details in `docs/design.md`):
1. A brute-force vs. ranked token/accuracy experiment.
2. A red-team test of the hallucination and injection defenses.
3. CRS-vs-GAO corroboration and conflict detection.
4. Table-to-CSV export.
5. Copy-ready citations.
6. Near-free follow-up questions on the same case.
7. Report version-change alerts.
8. A timed study with a real user.

I'd avoid adding more LLM agents.

---

# 2. Web dashboard

## User prompt

```text
Build the web dashboard next (FastAPI backend + React frontend) so a user can ask questions, watch the agents live, and view the evidence brief.
```

## Assistant response

I built the dashboard on the hw5 pattern.

**Backend** (`backend/main.py`, FastAPI):
- Runs the team as a background task, one request at a time (cost control).
- Routes: `/api/health`, `/api/cases` (POST to ask, GET to list), `/api/cases/{id}` (budget, shortlist, brief, run usage), and `/api/events?case_id=&after=` for live polling of the append-only audit trail. `backend/events.py` turns each audit entry into a one-line summary.
- Serves screenshots and briefs under `/files/...` and cached source PDFs at `/api/reports/{id}/pdf#page=N`. Path traversal is blocked.
- Added 3 system-only MCP tools (`list_cases`, `catalog_info`, `report_pdf_path`); agents can't see them. `get_brief` gained `include_quotes`. Brief findings now carry `report_id`.

**Frontend** (`frontend/`, React + Vite + TypeScript):
- Ask panel with example questions, and a list of past requests.
- Case header with hard-limit meters (searches, reports opened, pages read) plus findings and token stats.
- **Live: agents at work**: four lanes (working / waiting on teammate / done, with model calls and tokens) and a feed of major steps.
- **Evidence brief** tab: the team reply, the headline, and sections of evidence cards. Each card has the claim, quote, citation, a highlighted screenshot (click to enlarge), Open PDF at page, Official page, Copy quote + citation, and a quote-match badge.
- **How the team worked** tab: tokens by agent, reports considered, and the full audit trail.

FastAPI serves the built app from `frontend/dist` (committed), so graders only need Python: `uvicorn backend.main:app --port 8010`, then open http://localhost:8010.

**Verified in the browser:**
- The existing case renders.
- A new request started from the UI showed live lanes and feed, then switched to the brief automatically when it finished.
- All PDF, brief, and screenshot links return 200. The lightbox works. The mobile layout has no horizontal overflow, and the console has no errors.
- A fresh-copy install took about 18 s, and the server starts with the index built automatically.

Dashboard run: *How has the Navy frigate (FFG-62) program changed in cost and schedule?* It took 84 s and 140,614 tokens (67% of input cached), opened 1 report, read 7 pages, and verified 4 findings. It found the lead ship's delivery estimate slipping from July 2026 to April 2029 (33 months) and CBO's estimate that the first 10 ships would cost 40% more than Navy estimates.

Notes:
- `PORTKEY_API_KEY` is also exported in the shell profile, so the app picks it up even without a `.env` file.
- In a fresh clone, Past requests starts empty, because case history lives in the git-ignored index database. The briefs and screenshots in `output/` are still committed.

---

# 3. Multi-domain testing, newest-first research, and dashboard redesign

## User prompt

```text
I'd like you to test multiple domain areas, not just shipbuilding. Suppose I was interested in US healthcare costs and outcomes as they relate to public policy decisions. What impact did passing Obamacare have on the federal budget?

Prove to yourself the agents are consistently effective, and then I'll test them myself
```

## User prompt (sent while the assistant was working)

```text
I think the agents should be careful to use the most recent information possible before using older information. Please modify any tools to prioritize the newest information possible
```

## User prompt (sent while the assistant was working)

```text
I want the agents to be able to access older reports (even if they don't have summaries). They should just be accessed after newer options have been exhausted (essentially work from newest to oldest).

When you make these modifications, please iterate on the dashboard to make it cleaner and more human-friendly. I want someone who has never seen or thought about this use case to find the dashboard and intuitive.
```

## Assistant response

**Answer to the ACA question.** Across 6 runs, the team consistently found CRS R41664 (p. 7). It reports CBO/JCT's February 2011 estimate that the ACA would reduce federal deficits by $210 billion over FY2012–FY2021, with $1,390 billion in gross coverage costs offset by $1,252 billion in new revenues and savings. One run also traced the March 2010 estimate ($138 billion over 2010–2019) and the March 2012 update. Every run labeled these as projections. The brief notes automatically that the newest source is from 2012.

**Multi-domain data.**
- The sample was defense-heavy: it held only 8 of the 118 ACA titles. I rebuilt it across seven policy domains, for 10,731 reports.
- An offline, zero-token crawl (`scripts/enrich_catalog.py`) added CRS abstracts and revision counts. Revision count became an authority signal.
- An `--html` pass added summaries for newer reports, bringing coverage to 99%.

**Newest first (2nd message).**
- Recency weight raised to 0.20, with a 4-year decay. Older reports in the same series are penalized when a newer one covers the same concepts.
- Within a report, pages and passages with the latest years rank higher.
- Findings carry report dates. Briefs list the newest evidence first and add a freshness caveat on their own when every source is more than 3 years old.
- All five prompts now say the newer source wins.

**Newest to oldest (3rd message).**
- Search splits the catalog into eras (≤3, 3–8, 8+ years) and recalls candidates separately in each, so older title-only reports are never crowded out.
- It fills results from the newest era with strong matches, moves to older eras only after those run out, and reports how many strong matches remain per era. Searching again continues down the eras.

**Fixes from reading every brief.**
- Quotes must be real sentences, not table fragments.
- Search reports `match_quality`, so off-topic questions get `no_evidence` cheaply (Mars question: 77k → 22–50k tokens).
- The clarifying question is no longer duplicated.
- Prompt examples are domain-neutral.

**Evaluation** (`scripts/eval_suite.py`, `docs/evals.md`): 10 questions per run, 3 full runs, 29/30 passed. All 6 ACA runs gave the same source and headline. The median evidence question took about 92–106k tokens (about 70% cached) and 64–87 s. The one failure, energy credits in run 3, verified only 1 fact. Working newest to oldest, the Reader opened a 2026 report with no cost figures, and the Fact-checker correctly rejected its off-topic finding. I recorded that as a real miss and didn't loosen the test.

**Dashboard redesign** ("Gov Report Finder"):
- Question-first home page with topic example chips and a 3-step "how it works" strip.
- Plain-language progress tracker (Understanding → Finding reports → Reading → Fact-checking → Writing) with a "Now:" line and an elapsed timer.
- The answer comes first, with its status in plain words and a freshness note.
- Evidence cards show the claim, quote, source title and type, a date with an age badge, "View highlighted page", "Open PDF at page N", and "Copy quote + citation".
- "Ask again" flows for clarification and no-evidence answers.
- A "Behind the scenes" tab with limits, reports considered, AI usage, and the full log. A "How it works" explainer covers sources, the team, newest-first, trust checks, and limits.
- Verified in the browser at desktop and mobile widths.

---

# 4. Final report

## User prompt

```text
I'd like to work on the final report (as described on the assignment page).

It should be 10-15 pages, and written to help an unfamiliar human manager use the tool. The pages should be measured like standard printed pages with normal font and margins. Do not include filler content, just be thorough with the sections I describe below. Think of it as tool documentation. Please include app screenshots whenever it would be helpful for the reader. The report should be a clickable html page that we'll share on GitHub later.

Here are the sections you should include and what goes in each section:

1. Title & Links: A clever project name, a placeholder for a future GitHub repo link, and one sentence describing the product
2. Motivation: Describe the problem I laid out to you, and how this product saves substantial human effort
3. What is New Here: Explain how this is different from existing apps (emphasizing the screenshots for quick manual verification and the team of agents to minimize token usage)
4. The Agents: Explain each agent's job, the tools they use, how they're organized (draw an include a diagram), which model they use, and a step-by-step walkthrough of a sample run with screenshots.
5. Tokenomics: How many tokens does a typical run use? Measure this with real runs. Estimate how much this would cost a user to do one research task per workday over the period of a month. What would it cost to roll out this tool to a team of researchers? Are there scaling synergies? Also demonstrate how the team of agents reduces cost - share an example of one unoptimized agent doing the same task manually and show how our product is cheaper
6. Safety: Explain how this tool could be misused or attacked, and what measures are in place to prevent this. Address direct attacks, agent malfunctions, and runaway spending. Demonstrate at least one defense measure working with a screenshot
7. Impact: Estimate how long a person would take to accomplish one run of the agents. Apply a reasonable labor rate (like a shipyard PMO analyst salary for shipbuilding questions) to that time and compare against the agents' token cost. Make the business case clear
8. Limits & Next Steps: Explain what doesn't work well with the agents and what we could add with more time and effort
```

## Assistant response

Built `report.html`: 15 printed pages (US Letter, 1-inch margins, 11 pt), self-contained, with 9 embedded app screenshots, an architecture diagram, and a token chart. It is generated by `scripts/build_report.py` from the saved run data, so every number traces back to a file. The project is named **PinCite** (a "pin cite" is a citation to the exact page), and the dashboard was renamed to match.

New measurements for the report:
- Final 10-question suite (run 4).
- A **single-agent baseline**.
- A 20-check **red-team suite** (`scripts/safety_tests.py`), including live injection and budget-stop runs.
- A scripted **walkthrough** run captured with Playwright (Highway Trust Fund: answered in 65 s, 101,722 tokens, about $0.0065).

The typical question uses about 85k input tokens (64% cached) and 3.8k output, about $0.0064. That is $0.13 per user per month at one question per workday, $13 for 100 users, and about $1,338 for 10,000. Impact model: about 74 analyst minutes by hand vs. 5 minutes of review (BLS management-analyst pay, loaded to $69.96/hr), so about $80 saved per question.

**Honest finding:** the single unoptimized agent was **1.8–5.9× cheaper** in tokens than the team, and its quotes also verified. The team's cost is coordination overhead, not reading. The report says so plainly, explains what the extra cent buys, and proposes a "lean mode" as the next step.

Fixes made along the way:
- Batch tools: −11% requests, −7% median tokens, and more verified facts. The batch validation bug was fixed.
- The numbers-check bug found by the red team ("$500" matched "$51.9").
- Number-copying and re-record prompt fixes.
- Ages shown in months.
- "Working…" status for in-flight runs.

---

# 5. Lean mode, a cheaper team, and the slide deck

## User prompt

```text
Build a lean mode (one agent + the deterministic verifier and screenshots for simple questions), measure it against the baseline, and update the report.
```

## User prompt (sent while the assistant was working)

```text
And find efficiencies so the team works cheaper. For example, cache tokens that could be useful across the agents
```

## User prompt (sent while the assistant was working)

```text
When you complete the next draft of the report, please draft the HTML slide deck - as described on the assignment webpage.

The deck should be no more than 10 slides. The assignment page provides a sample structure, but you're welcome to use your own if it works better for this application. I will check the slides myself, so please be sure to verify all information that you report.

Make sure the slide deck is readable when we later upload it to my GitHub repo - it should not depend on offline files. Use reveal.js to make the slides look nice on the webpage
```

*(A screenshot of the assignment's slide-deck section was attached: at most 10 slides, reveal.js from a CDN, images embedded, suggested order Title → Problem → What's new → How agents work → Demo → Cost → Safety → Impact → What's next.)*

## Assistant response

**Three modes, picked on the home page:**
- **Quick:** one "Solo researcher" agent with at most 2 searches, 2 reports, and 6 pages. Code then verifies each quote, renders the screenshots, and writes the reply from verified facts only.
- **Full team:** the original four agents.
- **Auto (default):** runs Quick first and hands the question to the full team only if fewer than 2 facts verify.

Any partial answer also offers a **Dig deeper with the full team** button.

**Measured on October 25, 2026** (same evidence questions, medians):

| Mode | Cost per question | Verified facts |
|---|---|---|
| Unoptimized single agent | $0.0017 | 83% of its quotes pass the verifier; no screenshots |
| Quick | $0.0017 | Every fact verified, with screenshots |
| Auto | $0.0022 (1.3× the single agent) | Passed all 10 test questions; 5 of 8 answered in full; 3.1 facts on average; 27 s |
| Full team | $0.0048 | — |

Before these changes, every question went to the full team at 3.2× the single agent's cost.

**Making the team cheaper:**
- Explicit prompt-cache breakpoints after each agent's fixed instructions (30-minute lifetime). Agents' first requests went from 0% to 87% cached, and full-team cost fell 23%; priced the other way, the saving is 20%.
- Fewer model calls.
- We also tested one shared cached "handbook" for all agents, to reuse tokens across agents. It produced **no cross-agent cache hits**, so the default stays one cache per agent. The experiment is written up in `output/evals/cache_experiments.md`.

**Bugs found and fixed while testing lean mode:**
- A lean reply cited a fact the verifier had rejected.
- Headlines over 300 characters crashed the brief.
- Auto escalated every partial answer, which made it cost more than the team.
- The number check missed "FY2027"-style numbers.

**Report:** `report.html` was rewritten for the modes and caching (14 printed pages). The safety suite was re-run: 20 of 20 checks passed.

**Slides:** `slides.html` has 10 slides built with reveal.js 5.2.1 from the jsDelivr CDN. All screenshots and the agent diagram are embedded, so the file stands alone (2.7 MB). `scripts/build_slides.py` fills every number from the same `metrics()` function as the report, so the deck and report cannot disagree. Each slide was rendered at 1280×720 and checked for overflow, broken images, and console errors.

Fact-checking the deck caught one overstated figure. The "team before caching vs. single agent" ratio had mixed two pricing bases, which made it read 3.7×; on one basis it is 3.2×. The report and slides now both say 3.2×. The baseline is also now labeled correctly as 7 questions, against 8 runs for the PinCite modes.

---

# 6. Testing like a GitHub visitor, and README fixes

## User prompt

```text
I'd like to test the tool like someone accessing it through my eventual public GitHub repo. Can you start it up?
```

## Assistant response

I made a fresh copy of exactly the files Git would publish, outside the class folder: no `.env`, no search index, no case history. Then I followed the README word for word: create the virtual environment, `pip install -r requirements.txt`, `cp .env.example .env`, then `uvicorn backend.main:app --port 8010`.

Every step worked on Python 3.14. The search index (10,731 reports) built itself on first start, and the dashboard opened at http://localhost:8010 with an empty question history and no browser errors.

Problems a visitor would hit:
- The README's walkthrough still described the old dashboard.
- Its run times ("2–3 minutes") and tool count were out of date.
- About 600 test files in `output/` would be published.

## User prompt

```text
Yes, update the stale README sections to match the current dashboard and timings.
```

## Assistant response

I rewrote the README's dashboard walkthrough to match the current screens:
- **Ask:** the mode picker.
- **Watch:** the progress tracker.
- **Read and check:** the status line, evidence-card buttons, "Dig deeper", and the printable report.
- **Behind the scenes.**

I checked every label against the frontend code. Other fixes:
- Run times replaced with medians measured on October 25: Quick about 25 s; Auto about 30 s, or about 85 s when it hands off to the team; Full team about 75 s.
- Tool count corrected to 18 agent tools + 6 system-only tools.
- Added `--mode lean` for the command line and `--mode` for `eval_suite.py`.
- Exact catalog counts.
- A note to create `data/library/` if it's missing.

---

# 7. What matters most, a visible workflow, and accuracy

## User prompt

```text
I think the agent team over-rotated on new reports. For example, when I ask about shipbuilding cost overruns, the team focuses on relatively low-cost ancillary programs. The agents should be smart enough to look at both most recent data overall, as well as digging a bit deeper to get flagship programs like SSN, SSBN, CVN, and DDGs. Please combine both new reports and some prioritization to focus on the most costly (or generally important) programs/updates related to the given question. Perhaps make the first agent prioritize programs/updates to look at (in addition to a quick sweep of new reports)

Also, I'd like you to revise the report, slides, and dashboard itself to make it a bit clearer how the workflow progresses. Make it clearer how the auto mode lets one agent run first, then the team works in a quick mode, followed by the full team if needed. I see how each phase/model is listed, but it's not obvious to a new reader how the workflow actually progresses. Make this prominent on the dashboard, report, and slides.

The dashboard works well, and the report and slides are a good draft. Can you confirm each is updated to be consistent with how the current iteration of the agent team works (e.g., make sure the query modes and new workflow information are all accounted for in the documentation).

Update the README and AI_prompts files to be consistent with all our most recent work
```

*(Clarifying question from the assistant: Auto had no "team in quick mode" step, so which workflow should it have? The user chose: "Plan, quick pass, team (Recommended)", meaning ① the first agent plans priorities plus a sweep of the newest reports, ② a quick pass that code checks, and ③ the full team only if fewer than 2 facts verify.)*

## User prompt (sent while the assistant was working)

```text
Also, since the team of agents is marginally (~1.3x) more expensive than one agent alone - please emphasize the improvement in verifiable fact accuracy by the team of agents in the report and slides
```

## User prompt (sent while the assistant was working)

```text
Are they finished?
```

## Assistant response

**Diagnosis.** On the ship question, every one of the 8 earlier saved runs opened only one of the four flagship-program reports (the Ford-class carrier). The rest of the reading went to the newest reports on small programs: the LPD-17/LHA amphibious ships, the TAO-205 oiler, the FFG-62 frigate, and the LCS. Newest-first ranking had no notion of "what matters most".

**Plan step (new).** The first agent now names 1–4 priorities: the programs, laws, or updates with the largest cost or stakes. For example: Columbia SSBN-826, Virginia SSN-774, Ford CVN-78, DDG-51. `search_reports(priorities=...)` adds a zero-token **priority lane** next to the newest-reports lane. For each priority it picks the report whose title names it best, preferring reports CRS revises most (137–204 revisions for flagship programs, against 31–71 for small ones) and the most recent.
- The plan is saved on the case board, and each shortlisted report is tagged *Priority* or *Newest*.
- With the plan step, the ship question opened 2 of the 4 flagship reports in each mode (Columbia SSBN and Ford CVN; one Full-team run also reached Virginia SSN). In one run the answer led with CBO's estimate that the first Columbia-class submarine will cost $2.0 billion more than the Navy's estimate.

**Workflow made prominent.** The dashboard draws ① Plan → ② Quick pass → ③ Full team only if fewer than 2 facts verify → Answer in three places:
- on the home page, for the chosen mode;
- as the live tracker ("Step 2 of 3: Quick pass", with the plan's priorities and the verified count);
- above every answer ("How this answer was made").

Behind the scenes shows the research plan, and each answer now has a `#case-…` link. The report has a new three-step diagram and a "How a question moves through the workflow" section. The slides show it on slide 4 and in the demo screenshots.

**Accuracy emphasized.** The report has a new section, "What the team's design buys: verified facts", and slide 7 is now "Cost vs. accuracy". All three setups were scored by the same code check:
- **Single agent:** 5 of its 29 quotes (17%) fail the page-and-number check, including all 4 on the ship question, and it shows no screenshots.
- **PinCite:** its agents slip too (code held back 4 of 25 quotes in Auto), but **0 unverified facts reached the user** in any mode, and every fact has a highlighted page.
- **Cost:** Auto now costs about the same as the single agent ($0.0017 median, 1.0×; it was 1.3× before this round). The single agent produced slightly more passing quotes per question (3.4, against 2.6 for Auto and 3.0 for the team). The report states this trade-off plainly.

**Bugs the new suites and red team found, all fixed and re-tested:**
- **Lost answers:** a headline with an unsupported year could end a run with no answer. After one retry, code now removes just that sentence.
- **Race past the report limit:** parallel tool calls could open 5 reports against a limit of 4. Budgets are now reserved atomically.
- **Report limit bypass:** a refused report could still be read, because its pages were indexed from another question. Reading and quoting now require the report to be opened in the current question.
- **False rejections:** footnote numbers glued to sentences made faithful quotes fail; this explained 9 of 13 near-miss rejections.
- **Quick pass over its limit:** one quick pass ran 6 searches against an instructed limit of 2. The limit is now enforced in code, and a "no evidence" quick pass goes to the team when the catalog has on-topic reports.
- **Poisoned report through the priority lane:** the red team's planted "CRS report" reached the shortlist because its fake title matched a priority. Unvetted library PDFs can no longer take a priority slot.

**Final measurements.**
- Test suites: Auto 10/10, Quick 10/10, Full team 9/10 (one ACA run verified only 1 fact).
- Red team: 22/22 checks passed.
- Report: rebuilt at 15 printed pages. Slides: 10 slides, no overflow.
- Every number comes from the saved runs through `scripts/build_report.py`. README, `docs/design.md`, and `docs/evals.md` are updated to match.

---

# 8. Publishing the public GitHub repo

## User prompt

```text
Please commit this folder to a public GitHub repo, as done for other assignments in this class (and described in the final project assignment). Add the repo url that others can access to the header of both the report and the slides, so I can submit the two html files only
```

## Assistant response

I published the project as a **public** repository, **https://github.com/tom-barbaro-som/mgt409-pincite**. It follows the naming of the earlier class repos (`mgt409-hw4`, `mgt409-hw5`) and the assignment's repo rules: all the code, a README with step-by-step run instructions, and a `.env.example` with no real values.

**Before publishing:**
- Scanned every file to be committed for the real Portkey key and common secret patterns. None were found; the only pattern hits were base64 image fragments in the report catalog. No `.env` file is committed.
- Excluded `output/briefs/` and `output/evidence/` with `.gitignore`. These are per-question artifacts (about 1,100 files) that the app regenerates on every run, and the report and slides already embed the screenshots they use.
- Kept all the measurement data the report cites: `output/evals/`, `output/runs/`, `output/safety/`, and `output/audit_trail.json`.

**Repo link added to the deliverables:**
- **Report:** the header ("GitHub repo: …") and the Quick start, which now begins with `git clone` and `cd mgt409-pincite` and includes `cp .env.example .env`.
- **Slides:** the title slide and the closing slide.
- **README:** a repository line and clone steps.

Both HTML files are self-contained, with every screenshot embedded, so they can be submitted on their own. The report is still 15 printed pages, and the deck is 10 slides with no overflow.
