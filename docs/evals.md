# Multi-domain evaluation

`scripts/eval_suite.py` runs a fixed 10-question set through the real agent team (gpt-6-luna, sample dataset). It scores each run automatically and saves the results to `output/evals/<timestamp>.json` and `.md`. Each run is an ordinary case, so its brief can be opened in the dashboard.

**Questions** span health × budget (the ACA question, asked twice to test consistency), Medicare drug pricing, student loans, the Highway Trust Fund, immigration courts, energy tax credits, and defense shipbuilding. Two judgment checks are included: a vague request, which should get a clarifying question, and an out-of-scope request about a Mars colony, which should get "no evidence" without wasting tokens.

**Automatic checks per run:**
- The run completed.
- The reply status is acceptable for the question.
- At least 2 findings were verified (evidence questions only).
- Cited titles or quotes contain the question's topic terms.
- Every cited fact has a screenshot.
- There were no errors.

These checks test structure. **Accuracy was checked by reading every brief.**

## Results across three full runs (2026-10-21)

| Run | What changed | Pass | Median tokens per evidence question | Cached input | Median time |
|---|---|---|---|---|---|
| 1 `20261021-163243` | multi-domain sample + crawled abstracts | 10/10 | 91,691 | 71% | 64 s |
| 2 `20261021-165427` | + quote rules, `match_quality`, recency weighting | 10/10 | 100,367 | 69% | 77 s |
| 3 `20261021-171308` | + newest-to-oldest tiered search, HTML abstracts | 9/10 | 106,364 | 69% | 87 s |

**Consistency.** All 6 ACA runs cited the same core source, CRS R41664 p. 7, and the same headline figure: CBO/JCT's February 2011 estimate that the ACA would reduce deficits by **$210 billion over FY2012–FY2021**. Every run labeled it a projection, not an actual outcome. The vague request got a clarifying question in all 3 runs, for about 1.5–1.8k tokens and under 2 s.

**Newest information first.** In runs 2–3, the newest cited sources were Highway Trust Fund 2026, student loans 2025, immigration 2025, ships 2025–2026, Medicare 2023, and energy 2024. The ACA budget question still rests on 2012. CRS hasn't published a newer overview of the law's budget effects, and the brief says so automatically: *"the newest source is 14 years old; more recent figures may exist outside these reports."*

## Problems found by reading the briefs, and the fixes

| Problem (run) | Fix | Verified in |
|---|---|---|
| A table fragment (`"FY2023-FY2027: $13.1"`) was verified as a quote, and its claim named a credit that wasn't in the quote (1) | Quotes must be ≥ 40 characters and ≥ 6 words (a real sentence) | runs 2–3: no fragments |
| The Mars question returned "partial" from tangential 2006 NASA facts, for 77k tokens (1) | Search reports `match_quality`; the Coordinator answers `no_evidence` without reading when matches are weak | runs 2–3: `no_evidence`, 22k and 50k tokens |
| The clarifying question was repeated inside the reply (1) | Prompt: the question goes only in `clarifying_question` | runs 2–3 |
| The sample catalog was defense-only (8 of 118 ACA titles) | Seven-domain sample: 10,731 reports | run 1 |
| Titles only: policy-impact questions ("budget effects") matched poorly | Offline crawl of CRS abstracts plus HTML summaries for newer reports (99% coverage) | runs 1–3 |
| Abstracts existed only for pre-2018 reports, so older reports outranked newer ones | HTML summary pass; title matches weighted above abstract matches; per-era recall | run 3 |

## Known misses (honest)

- **Energy credits, run 3 (failed the ≥2-facts check):** working newest to oldest, the Reader opened a 2026 Insight on the 2025 law (OBBBA) first, but found no cost figures in the pages it read. The Fact-checker rejected the off-topic finding that came out of it and one duplicate, leaving 1 verified fact: JCT's $271 billion enactment estimate. A deeper read of the 2026 report, or the full catalog, might find updated costs.
- **ACA:** in the dashboard run, the Researcher saw a 2026 report on the enhanced premium tax credit but judged it off-question. That report covers the cost of extending one subsidy, not the effect of passing the law. This is defensible, but a user who wants current ACA spending should ask that directly.
- **Shipbuilding** is the most expensive question (150–190k tokens), because cost-growth data is spread across many program reports.

Total spend for the three full runs: about 2.76M tokens (30 runs). Running it again costs about 1M tokens.

## Update, October 25: batching, single-agent baseline, red team

- **Run 4 `20261025-145231`** (batch tools: `record_findings`, `check_findings`, `decide_findings`, and `open_report` with ranked pages): 9/10 passed. Median requests per evidence question fell from 26.5 to 23.5, and median tokens from 106k to 99k (−7%). Verified facts per question rose from 2.25 to 3.38. The one failure (student loans) was a batch bug: one malformed item failed the whole call's schema. Validation is now per item, and the re-run `20261025-150232` passed with 4 verified facts.
- **Single-agent baseline** (`scripts/baseline_single_agent.py`, `baseline-20261025-144737.json`): one unoptimized agent with keyword title search and whole-page reading. It used **1.8–5.9× fewer tokens** than the team on the ACA, ships, and Highway Trust Fund questions, and all 13 of its quotes passed the verifier. The team is not cheaper. Its cost is coordination (about 24 requests, each re-sending about 2k tokens of instructions and tools plus history). It buys the independent fact-check, screenshots, newest-first ordering, caps, and the audit trail.
- **Red team** (`scripts/safety_tests.py --live`): 20/20 checks passed. The first run found a real bug: the numbers check compared digit substrings, so "$500" matched inside "$51.9 billion". It now compares numeric values. In the worst-case injection test, with a poisoned "CRS report" as the only source, 0 injected claims were recorded or verified. The 15k-token budget stopped the team at about 20k (the cap is checked after each request).
- **Prompt fixes from the walkthrough:** copy numbers exactly as the quote writes them, and when a finding is re-recorded, verify the corrected copy and reject the old one. An earlier ships run lost CVN-78's 27.0% figure because the Fact-checker rejected the corrected duplicates instead.

## Update, October 25 (evening): plan step, workflow view, final suites

**What changed.**

- **Plan step.** The first agent names 1–4 priorities (the programs or updates with the largest cost or stakes). `search_reports(priorities=...)` adds a **priority lane** next to the newest-reports lane: for each priority, the report whose title names it best, preferring the most-revised and most recent.
- **Workflow view.** The dashboard shows Plan → Quick pass → Full team only if needed → Answer on the home page, while a question runs, and above each answer. Answers also have `#case-…` links.

**Fixes found by these suites and the red team** (all re-tested):

- The quick pass's 2-search limit is now enforced in code.
- A "no evidence" quick pass goes to the team when the catalog has on-topic reports (best concept coverage ≥ 0.4; off-topic questions score ≤ 0.2).
- After one retry, code removes a sentence with an unbacked number instead of failing the run.
- Case budgets are reserved atomically. Parallel `open_report` calls had opened 5 reports against a limit of 4.
- Reading, page ranking, and quoting require the report to be opened in the current case. A refused fifth report had still been read and quoted because its pages were indexed from another case.
- Quote matching ignores footnote markers glued to sentences ("MFP.78 The"). These explained 9 of 13 near-miss rejections.
- A hand-added library PDF can't take a priority slot from a catalog report. The planted "CRS report" had been shortlisted through the priority lane. Replaying the final runs' plans showed this rule changes none of their 61 priority picks.

**Final suites** (used by `report.html` and `slides.html`):

| Mode | File | Passed | Fully answered | Verified facts (avg) | Median time | Median cost | Escalated |
|---|---|---|---|---|---|---|---|
| Auto | `20261025-184027-auto.json` | 10/10 | 4 of 8 | 2.6 | 21 s | $0.0017 | 0 |
| Quick | `20261025-184339-lean.json` | 10/10 | 5 of 8 | 2.8 | 20 s | $0.0020 | — |
| Full team | `20261025-185513-team.json` | 9/10 | 6 of 8 | 3.0 | 72 s | $0.0056 | — |

The Auto and Quick suites ran before the last two fixes. Neither suite read an unopened report, and neither run's plan picks change under the provenance rule, so their results stand for the final code.

- **Single-agent baseline** (`baseline-20261025-153248.json`, same day): $0.0017 median. 5 of its 29 quotes (17%) fail the same page-and-number check, including 4 of 4 on the ship question.
- **Shown to the user:** 0 unverified facts in Auto, 0 in Quick, and 0 in Full team. Code held back 4 of 25 recorded quotes in Auto and 14 of 38 in Full team.
- **Flagship ship programs:** Before the plan step, all 8 saved runs of the ship question opened only 1 of the four flagship reports (the Ford-class carrier). With the plan step, step 1 named 2–3 of the four flagship programs in each mode, and the agents opened 2 of 4 (Columbia SSBN, Ford CVN) in Auto, 2 of 4 (Columbia SSBN, Ford CVN) in Quick, and 2 of 4 (Columbia SSBN, Ford CVN) in Full-team mode. Verified facts came from 2 of them in Auto, 1 in Quick, and 1 in Full-team mode.
- **Check failure:** Full team on one ACA run ended with 1 verified fact (variance; the other ACA run verified 4).
- **Variance:** across the evening's repeated suites, the same question sometimes moved between Answered and Partly answered. When Auto handed off to the team in earlier suites, it took 30–146 s.
- **Red team** (`scripts/safety_tests.py --live`): 22 of 22 checks passed, including the parallel-limit race, the read-without-open check, and the poisoned report, which was never shortlisted.
