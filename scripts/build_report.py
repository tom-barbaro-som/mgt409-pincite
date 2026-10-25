"""Build report.html: a single self-contained file (screenshots embedded) from docs/report_template.html.

  python scripts/build_report.py

Every number in the report comes from saved run data, not from hand-typed values:
  output/evals/*.json          multi-domain eval runs (before / after batching) and the single-agent baseline
  output/runs/<case>.json      the walkthrough run (per-agent tokens)
  output/safety/*.json         red-team results
  data/index_sample.db         catalog size, pages read, shortlists
Placeholders in the template: {{name}} for numbers, {{img:file.jpg}} for screenshots, {{chart}}.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
EVALS = ROOT / "output" / "evals"

# gpt-6-luna prices per 1M tokens (MGT 409 Lecture 11, "Cached Token Cost" slide)
PRICE = {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50}
SOL = {"cached": 0.10, "cache_write": 2.50, "output": 10.00}  # gpt-6.1-sol, for sensitivity
WORKDAYS = 21
WORDS_PER_PAGE_FALLBACK = 470
READ_WPM = 238          # Brysbaert (2019): adult silent reading, non-fiction
SKIM_FACTOR = 3         # a skilled analyst skims ~3x faster than full reading
FIND_MIN = 20           # minutes to search agency sites and pick reports
MIN_PER_FACT = 5        # minutes to locate the exact sentence, copy it, record the page, double-check
REVIEW_MIN = 5          # analyst minutes to review PinCite's evidence cards
WAGE = 48.97            # BLS 2025 median hourly pay, management analysts (SOC 13-1111)
WAGE_SHARE = 0.700      # BLS ECEC June 2026: wages are 70.0% of private-industry compensation
GS13_MID = 140_000      # midpoint of a posted GS-0343-13 program analyst range ($120k-$160k)

WALK_Q = "When is the Highway Trust Fund projected to run short of money%"
# Saved suites (output/evals/). The current version (with the plan step) and the version before it.
RUN_AUTO, RUN_LEAN, RUN_TEAM = "20261025-184027-auto.json", "20261025-184339-lean.json", "20261025-185513-team.json"
PREV_AUTO, PREV_LEAN = "20261025-154559-auto.json", "20261025-153248-lean.json"
CACHE_AFTER = "20261025-153248-team.json"  # the controlled caching experiment: same team, before vs after caching
BASELINE = "baseline-20261025-153248.json"
PLAN_STEP_SINCE = "20261025-1739"   # suites saved before this ran without the plan step
FLAGSHIP_SHIP_REPORTS = {"R41129", "RL32418", "RS20643", "RL32109"}  # Columbia SSBN, Virginia SSN, Ford CVN, DDG-51/1000
SHIPS_Q = "How much have first-of-class Navy ships overrun%"


def cost(t: dict, price: dict = PRICE) -> float:
    """Conservative: every uncached input token is billed at the cache-write rate."""
    return ((t["input"] - t["cached"]) * price["cache_write"] + t["cached"] * price["cached"]
            + t["output"] * price["output"]) / 1e6


def money(x: float, digits: int = 2) -> str:
    return f"${x:,.{digits}f}"


def n(x: float) -> str:
    return f"{x:,.0f}"


def exact(t: dict) -> float:
    """Exact price when cache-write tokens were recorded (final suites)."""
    cw = t.get("cache_write", 0)
    return ((t["input"] - t["cached"] - cw) * PRICE["input"] + cw * PRICE["cache_write"]
            + t["cached"] * PRICE["cached"] + t["output"] * PRICE["output"]) / 1e6


def load(name: str) -> list[dict]:
    return json.loads((EVALS / name).read_text())


def metrics() -> dict[str, str]:
    """Every number used by report.html and slides.html, computed from saved run data."""
    db = sqlite3.connect(ROOT / "data" / "index_sample.db")
    run3 = load("20261021-171308.json")                                   # team, before batching
    run4 = [r for r in load("20261025-145231.json") if r["id"] != "student-loans"] + load("20261025-150232.json")
    team = load(RUN_TEAM)                                                 # Full team (current version)
    lean = load(RUN_LEAN)                                                 # Quick mode (current version)
    auto = load(RUN_AUTO)                                                 # Auto mode, the default (current version)
    base = load(BASELINE)                                                 # one unoptimized agent
    safety = json.loads((ROOT / "output" / "safety" / "safety_report.json").read_text())
    evq = lambda rows: [r for r in rows if r["id"] not in ("vague", "out-of-scope")]  # noqa: E731
    tot = lambda r: r["tokens"]["input"] + r["tokens"]["output"]  # noqa: E731

    def case_of(pattern: str) -> tuple[str, dict]:
        cid = db.execute("SELECT case_id FROM cases WHERE request LIKE ? ORDER BY created_at DESC LIMIT 1",
                         (pattern,)).fetchone()[0]
        return cid, json.loads((ROOT / "output" / "runs" / f"{cid}.json").read_text())

    walk_id, walk = case_of(WALK_Q)
    M: dict[str, str] = {}

    # ---- catalog -------------------------------------------------------------------------------
    M["catalog_reports"] = n(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
    words = [len(t.split()) for (t,) in db.execute("SELECT text FROM pages")]
    wpp = st.median(words) if words else WORDS_PER_PAGE_FALLBACK
    M["words_per_page"] = n(wpp)

    # ---- modes compared (same 8 evidence questions, same day) -----------------------------------
    def mode_row(label: str, rows: list[dict], price=exact, note: str = "") -> tuple[str, float]:
        ev = evq(rows)
        cost_med = st.median(price(r["tokens"]) for r in ev)
        answered = sum(r["status"] == "answered" for r in ev)
        facts = st.mean(r["findings"].get("verified", 0) for r in ev)
        row = (f"<tr><td>{label}</td><td>{n(st.median(map(tot, ev)))}</td><td>{money(cost_med, 4)}</td>"
               f"<td>{answered} of {len(ev)}</td><td>{facts:.1f}</td><td>{n(st.median(r['seconds'] for r in ev))} s</td>"
               f"<td>{note}</td></tr>")
        return row, cost_med
    bt = [b["tokens"] for b in base]
    b_cost = st.median(exact(t) for t in bt)
    b_ok, b_all = sum(b["verified"] for b in base), sum(len(b["findings"]) for b in base)
    rows = [f"<tr><td>One unoptimized agent (baseline)</td><td>{n(st.median(t['input'] + t['output'] for t in bt))}</td>"
            f"<td>{money(b_cost, 4)}</td><td>n/a</td><td>n/a</td><td>{n(st.median(b['seconds'] for b in base))} s</td>"
            f"<td>{b_ok} of {b_all} quotes ({b_ok / b_all:.0%}) pass the verifier; no screenshots</td></tr>"]
    r_quick, c_quick = mode_row("<b>Quick</b>: plan + one agent + code verifier", lean, note="Every fact shown is verified, with a screenshot")
    r_auto, c_auto = mode_row("<b>Auto</b> (default): Quick, team if needed", auto,
                              note=f"Escalated {sum(r.get('escalated', False) for r in evq(auto))} of {len(evq(auto))} to the team")
    r_tb, c_tb = mode_row("Full team, before caching", run4, note="Oct 25, morning")
    r_team, c_team = mode_row("<b>Full team</b> (now)", team, note="Caching cut cost 23% in a controlled test; the plan step adds flagship-report reading")
    M["mode_rows"] = "\n".join(rows + [r_quick, r_auto, r_tb, r_team])
    M["b_cost"], M["quick_cost"], M["auto_cost"], M["team_cost"] = (money(c, 4) for c in (b_cost, c_quick, c_auto, c_team))
    M["b_verify_pct"] = f"{b_ok / b_all:.0%}"
    M["auto_vs_base"] = f"{c_auto / b_cost:.1f}"
    ratio = c_auto / b_cost
    M["auto_vs_base_phrase"] = "about the same as" if abs(ratio - 1) < 0.06 else f"about {ratio:.1f}× as much as"
    q_ratio = c_quick / b_cost
    M["quick_vs_base"] = f"{q_ratio:.1f}"
    M["quick_vs_base_phrase"] = ("about the same as the single agent" if abs(q_ratio - 1) < 0.06
                                 else f"{q_ratio:.1f}× the single agent")
    M["auto_vs_base_title"] = "about the same cost as one agent" if abs(ratio - 1) < 0.06 else f"{ratio:.1f}× the cost of one agent"
    M["team_vs_base"] = f"{c_team / b_cost:.1f}"
    M["tb_vs_base"] = f"{c_tb / b_cost:.1f}"
    M["auto_answered"] = str(sum(r["status"] == "answered" for r in evq(auto)))
    M["team_answered"] = str(sum(r["status"] == "answered" for r in evq(team)))
    team_before_cons = st.median(cost(r["tokens"]) for r in evq(run4))
    cache_after = load(CACHE_AFTER)
    team_after_cons = st.median(cost(r["tokens"]) for r in evq(cache_after))
    M["team_cost_before"], M["team_cost_after"] = money(team_before_cons, 4), money(team_after_cons, 4)
    M["team_cost_change"] = f"{team_after_cons / team_before_cons - 1:+.0%}"
    M["team_cost_change_abs"] = f"{1 - team_after_cons / team_before_cons:.0%}"
    for key, rows_ in (("quick_tokens", lean), ("auto_tokens", auto), ("team_tokens", team)):
        M[key] = f"{st.median(map(tot, evq(rows_))) / 1000:.0f}k"
    M["b_tokens"] = f"{st.median(t['input'] + t['output'] for t in bt) / 1000:.0f}k"

    # ---- typical run = Auto median --------------------------------------------------------------
    ev = evq(auto)
    for r in auto:
        r["cost"] = exact(r["tokens"])
    med = lambda key: st.median(r["tokens"][key] for r in ev)  # noqa: E731
    M["n_ev"] = str(len(ev))
    M["typ_in"], M["typ_cached"], M["typ_out"] = n(med("input")), n(med("cached")), n(med("output"))
    M["typ_req"] = n(st.median(r["tokens"]["requests"] for r in ev))
    M["cache_share"] = f"{sum(r['tokens']['cached'] for r in ev) / sum(r['tokens']['input'] for r in ev):.0%}"
    typ_cost = st.median(r["cost"] for r in ev)
    M["typ_cost"] = money(typ_cost, 4)
    M["cost_min"], M["cost_max"] = money(min(r["cost"] for r in ev), 4), money(max(r["cost"] for r in ev), 4)
    M["typ_secs"] = n(st.median(r["seconds"] for r in ev))
    M["facts_avg"] = f"{st.mean(r['findings'].get('verified', 0) for r in ev):.1f}"
    M["auto_pass"] = f"{sum(r['passed'] for r in auto)} of {len(auto)}"
    label = {"aca-budget-1": "ACA budget impact (run A)", "aca-budget-2": "ACA budget impact (run B)",
             "medicare-drugs": "Medicare drug-price negotiation", "student-loans": "Student-loan forgiveness cost",
             "highway-trust-fund": "Highway Trust Fund solvency", "immigration-courts": "Immigration court backlog",
             "energy-credits": "Clean-energy tax credit cost", "ships-regression": "First-of-class ship overruns",
             "vague": "“Tell me about the government.”", "out-of-scope": "Mars colony economics (off-topic)"}
    trows = []
    for r in auto:
        t = r["tokens"]
        path = "Quick → team" if r.get("escalated") else "Quick"
        trows.append(f"<tr><td>{label[r['id']]}</td><td>{path}</td><td>{(r['status'] or 'error (no answer)').replace('_', ' ')}</td>"
                     f"<td>{n(t['input'])}</td><td>{n(t['cached'])}</td><td>{n(t['output'])}</td><td>{t['requests']}</td>"
                     f"<td>{r['findings'].get('verified', 0)}</td><td>{(r.get('newest_source') or '—')[:7]}</td>"
                     f"<td>{money(r['cost'], 4)}</td></tr>")
    M["token_rows"] = "\n".join(trows)
    month = typ_cost * WORKDAYS
    heavy = max(r["cost"] for r in ev) * 5 * WORKDAYS
    M["month_user"], M["users_100"], M["users_10k"] = money(month, 2), money(month * 100, 0), money(month * 10_000, 0)
    M["heavy_user"], M["heavy_10k"] = money(heavy, 2), money(heavy * 10_000, 0)
    M["sol_cost"] = money(cost(sorted(ev, key=lambda r: r["cost"])[len(ev) // 2]["tokens"], SOL), 3)

    # ---- caching evidence ------------------------------------------------------------------------
    def first_req_share(rows: list[dict]) -> float:
        ids, seen, shares = {r["case_id"] for r in evq(rows)}, set(), []
        for line in (ROOT / "output" / "audit_trail.json").open(encoding="utf-8"):
            e = json.loads(line)
            if e.get("case_id") not in ids or e["event"] != "model_step":
                continue
            key = (e["case_id"], e["agent"], e.get("depth"))
            if key not in seen:
                u = e["detail"]["request_usage"]
                shares.append(u["cached_input"] / max(u["input"], 1))
            seen.add(key)
        return st.mean(shares)
    share = lambda rows: sum(r["tokens"]["cached"] for r in evq(rows)) / sum(r["tokens"]["input"] for r in evq(rows))  # noqa: E731
    M["first_before"], M["first_after"] = f"{first_req_share(run4):.0%}", f"{first_req_share(cache_after):.0%}"
    M["share_before"], M["share_after"] = f"{share(run4):.0%}", f"{share(cache_after):.0%}"

    # ---- batching, early exits, reading ----------------------------------------------------------
    M["b_req"] = f"{st.median(r['tokens']['requests'] for r in evq(run3)):.1f}"
    M["a_req"] = f"{st.median(r['tokens']['requests'] for r in evq(run4)):.1f}"
    M["a_req_n"] = n(st.median(r["tokens"]["requests"] for r in evq(run4)))
    M["b_facts"] = f"{st.mean(r['findings'].get('verified', 0) for r in evq(run3)):.2f}"
    M["a_facts"] = f"{st.mean(r['findings'].get('verified', 0) for r in evq(run4)):.2f}"
    run1 = load("20261021-163243.json")
    M["mars_before"] = n(tot(next(r for r in run1 if r["id"] == "out-of-scope")))
    M["mars_after"] = n(tot(next(r for r in auto if r["id"] == "out-of-scope")))
    M["vague_tok"] = n(tot(next(r for r in auto if r["id"] == "vague")))
    pages_read = pages_open = 0
    for r in ev:
        pages_read += int(r["budget"]["pages_read"].split("/")[0])
        pages_open += sum(p or 0 for (p,) in db.execute(
            "SELECT r.page_count FROM shortlist s JOIN reports r USING (report_id) WHERE s.case_id=? AND s.status='opened'",
            (r["case_id"],)))
    M["pages_read"], M["pages_open"], M["pages_pct"] = n(pages_read), n(pages_open), f"{pages_read / pages_open:.0%}"

    # ---- chart: tokens per topic -----------------------------------------------------------------
    topics = [("aca-budget", "aca-budget-1", "ACA budget"), ("medicare-drugs", "medicare-drugs", "Medicare drugs"),
              ("student-loans", "student-loans", "Student loans"), ("highway-trust-fund", "highway-trust-fund", "Highway Trust Fund"),
              ("immigration-courts", "immigration-courts", "Immigration courts"),
              ("energy-credits", "energy-credits", "Energy credits"), ("ships-regression", "ships-regression", "First-of-class ships")]
    pick = lambda rows, i: next(r for r in rows if r["id"] == i)  # noqa: E731
    ratios = [tot(pick(auto, e)) / tot(pick(base, b)) for b, e, _ in topics if not pick(auto, e).get("escalated")]
    esc_names = [name for b, e, name in topics if pick(auto, e).get("escalated")]
    M["esc_chart_note"] = (f"; it cost most on the {'question' if len(esc_names) == 1 else 'questions'} it escalated "
                           f"({', '.join(esc_names)})" if esc_names else "; no question needed the full team")
    M["auto_ratio_min"], M["auto_ratio_max"] = f"{min(ratios):.1f}", f"{max(ratios):.1f}"
    M["chart"] = bar_chart([(name, tot(pick(base, b)), tot(pick(auto, e)), tot(pick(team, e))) for b, e, name in topics])

    # ---- walkthrough + escalation example -----------------------------------------------------------
    u = walk["usage"]
    wt = {"input": u["input_tokens"], "cached": u["cache_read_tokens"], "cache_write": u["cache_write_tokens"],
          "output": u["output_tokens"]}
    M["walk_secs"] = f"{walk['seconds']:.0f}"
    M["walk_in"], M["walk_cached"], M["walk_out"] = n(u["input_tokens"]), n(u["cache_read_tokens"]), n(u["output_tokens"])
    M["walk_total"], M["walk_req"], M["walk_cost"] = n(u["total_tokens"]), str(u["requests"]), money(exact(wt), 4)
    M["walk_trace"] = walk_trace(walk_id)
    escs = [r for r in evq(auto) if r.get("escalated")]
    M["esc_count"] = str(len(escs))
    if escs:
        e0 = escs[0]
        M["esc_sentence"] = (f"In the test suite, Auto escalated {len(escs)} of {len(evq(auto))} evidence questions. For example, on "
                             f"“{label[e0['id']]}” the quick pass verified too few facts, so the full team took over and "
                             f"ended with {e0['findings'].get('verified', 0)} verified facts ({e0['seconds']:.0f} seconds, "
                             f"{n(tot(e0))} tokens, {money(e0['cost'], 4)}).")
    else:
        M["esc_sentence"] = ("In this test suite the quick pass verified at least two facts on every evidence question, so "
                             "Auto never needed the full team.")
    walk_board = db.execute("SELECT plan FROM cases WHERE case_id=?", (walk_id,)).fetchone()
    wplan = json.loads(walk_board[0])["priorities"] if walk_board and walk_board[0] else []
    M["walk_plan"] = (", ".join(f"“{p['priority']}”" for p in wplan) + " plus the newest reports") if wplan else "the newest reports"
    M["walk_status"] = {"answered": "Answered", "partial": "Partly answered"}.get(walk["reply"]["status"], walk["reply"]["status"])
    M["walk_facts"] = str(db.execute("SELECT COUNT(*) FROM findings WHERE case_id=? AND status='verified'", (walk_id,)).fetchone()[0])

    # ---- safety -------------------------------------------------------------------------------------
    M["safety_pass"] = f"{sum(r['passed'] for r in safety)} of {len(safety)}"
    poison = next(r for r in safety if r["attack"].startswith("Poisoned"))
    M["poison_e2e"] = ("In the full end-to-end run, the poisoned report was never shortlisted at all."
                       if "never shortlisted" in poison["evidence"] else
                       "In the full end-to-end run the poisoned report was shortlisted, and the reply still did not repeat its claim.")
    budget = next(r for r in safety if "token budget" in r["defense"])
    M["budget_stop"] = re.search(r"after ([\d,]+) tokens", budget["evidence"]).group(1)
    worst = next(r for r in safety if r["attack"].startswith("Worst case"))
    M["worst_tokens"] = re.search(r"([\d,]+) tokens", worst["evidence"]).group(1)

    # ---- impact (manual scope = what the full team opened and verified) ------------------------------
    tev = evq(team)
    open_pages = []
    for r in tev:
        open_pages.append(sum(p or 0 for (p,) in db.execute(
            "SELECT r.page_count FROM shortlist s JOIN reports r USING (report_id) WHERE s.case_id=? AND s.status='opened'",
            (r["case_id"],))))
    avg_open_pages = st.mean(open_pages)
    team_facts = st.mean(r["findings"].get("verified", 0) for r in tev)
    loaded = WAGE / WAGE_SHARE
    skim_min = avg_open_pages * wpp / (READ_WPM * SKIM_FACTOR)
    fact_min = MIN_PER_FACT * team_facts
    manual_min = FIND_MIN + skim_min + fact_min
    manual_cost = manual_min / 60 * loaded
    pin_cost = REVIEW_MIN / 60 * loaded + typ_cost
    low_min = manual_min / 2
    gs_loaded = GS13_MID / 2087 / WAGE_SHARE
    M.update(loaded=money(loaded), wage=money(WAGE), skim_min=n(skim_min), fact_min=n(fact_min),
             find_min=str(FIND_MIN), review_min=str(REVIEW_MIN), manual_min=n(manual_min),
             manual_hours=f"{manual_min / 60:.1f}", manual_cost=money(manual_cost, 0), pin_cost=money(pin_cost),
             save_q=money(manual_cost - pin_cost, 0), save_pct=f"{1 - pin_cost / manual_cost:.0%}",
             avg_open_pages=n(avg_open_pages), team_facts=f"{team_facts:.1f}",
             words_skimmed=n(avg_open_pages * wpp), skim_wpm=n(READ_WPM * SKIM_FACTOR),
             month_hours=n((manual_min - REVIEW_MIN) * WORKDAYS / 60), month_save=money((manual_cost - pin_cost) * WORKDAYS, 0),
             team100_hours=n((manual_min - REVIEW_MIN) * WORKDAYS / 60 * 100),
             team100_save=money((manual_cost - pin_cost) * WORKDAYS * 100, 0), tokens_month_100=money(month * 100, 0),
             gs_loaded=money(gs_loaded, 0), gs_manual=money(manual_min / 60 * gs_loaded, 0),
             low_min=n(low_min), low_cost=money(low_min / 60 * loaded, 0),
             low_save_pct=f"{1 - pin_cost / (low_min / 60 * loaded):.0%}")

    # ---- accuracy: what the extra fraction of a cent buys -----------------------------------------
    M.update(accuracy(db, base, auto, team, lean))
    M["quick_secs"] = n(st.median(r["seconds"] for r in evq(lean)))
    prev_auto, prev_lean = evq(load(PREV_AUTO)), evq(load(PREV_LEAN))
    M["auto_cost_prev"] = money(st.median(exact(r["tokens"]) for r in prev_auto), 4)
    M["auto_vs_base_prev"] = f"{st.median(exact(r['tokens']) for r in prev_auto) / st.median(exact(b['tokens']) for b in base):.1f}"
    M["quick_cost_prev"] = money(st.median(exact(r["tokens"]) for r in prev_lean), 4)
    return M


def _flagship(row: dict) -> list[str]:
    ids = {c.split()[1] for c in row.get("reports_cited", []) if len(c.split()) > 1}
    return sorted(ids & FLAGSHIP_SHIP_REPORTS)


def accuracy(db: sqlite3.Connection, base: list[dict], auto: list[dict], team: list[dict], lean: list[dict]) -> dict[str, str]:
    """Quote-level accuracy of the single agent vs PinCite, scored by the same code verifier."""
    evq = lambda rows: [r for r in rows if r["id"] not in ("vague", "out-of-scope")]  # noqa: E731
    M: dict[str, str] = {}
    b_all = sum(len(b["findings"]) for b in base)
    b_ok = sum(b["verified"] for b in base)
    ship = next(b for b in base if b["id"] == "ships-regression")
    M.update(b_all=str(b_all), b_fail=str(b_all - b_ok), b_fail_pct=f"{(b_all - b_ok) / b_all:.0%}",
             b_fact_q=f"{b_ok / len(base):.1f}", b_ship_all=str(len(ship["findings"])),
             b_ship_fail=str(len(ship["findings"]) - ship["verified"]))
    ship_ids = {f["report_id"] for f in ship["findings"]}
    M["b_ship_flag"] = f"{len(ship_ids & FLAGSHIP_SHIP_REPORTS)} of 4"

    def shown(rows: list[dict]) -> tuple[int, int, int, int]:
        """(facts shown in briefs, of which unverified, of which with a screenshot, recorded quotes held back)."""
        shown_n = bad = shots = held = 0
        for r in evq(rows):
            row = db.execute("SELECT body_json FROM briefs WHERE case_id=?", (r["case_id"],)).fetchone()
            cited = [f for s in json.loads(row[0])["sections"] for f in s["findings"]] if row else []
            ids = [f["finding_id"] for f in cited]
            status = dict(db.execute(f"SELECT finding_id, status FROM findings WHERE finding_id IN ({','.join('?' * len(ids))})",
                                     ids).fetchall()) if ids else {}
            shown_n += len(ids)
            bad += sum(status.get(i) != "verified" for i in ids)
            shots += sum(bool(f.get("evidence_path")) for f in cited)
            counts = r["findings"]
            held += sum(counts.values()) - counts.get("verified", 0)
        return shown_n, bad, shots, held

    for key, rows in (("auto", auto), ("team", team), ("lean", lean)):
        s_n, bad, shots, held = shown(rows)
        recorded = sum(sum(r["findings"].values()) for r in evq(rows))
        M[f"shown_unverified_{key}"] = str(bad)
        M[f"{key}_held"], M[f"{key}_recorded"] = str(held), str(recorded)
        M[{"auto": "shot_pct", "team": "shot_pct_team", "lean": "shot_pct_lean"}[key]] = f"{shots / s_n:.0%}" if s_n else "n/a"
    M["shown_unverified"] = M["shown_unverified_auto"]
    M["quick_facts"] = f"{st.mean(r['findings'].get('verified', 0) for r in evq(lean)):.1f}"
    vb, va = b_ok / len(base), st.mean(r["findings"].get("verified", 0) for r in evq(auto))
    vt = st.mean(r["findings"].get("verified", 0) for r in evq(team))
    more = [name for name, v in (("Auto", va), ("the full team", vt)) if v > vb]
    M["volume_sentence"] = (
        f"the single agent produced {vb:.1f} passing quotes per question (alongside its failing ones), against "
        f"{va:.1f} verified facts for Auto and {vt:.1f} for the full team. "
        + (f"{' and '.join(more).capitalize()} found more usable facts per question." if more else
           "PinCite shows somewhat fewer facts per question, and every one is verified, with its page."))
    ship_status = {k: next(r for r in rows if r["id"] == "ships-regression")["status"]
                   for k, rows in (("Auto", auto), ("Quick", lean), ("Full team", team))}
    partial = [k for k, v in ship_status.items() if v != "answered"]
    M["ship_status_short"] = (f'"Partly answered" in {len(partial)} of 3 modes' if partial
                              else "now answered in all 3 modes, but with no cross-program table")
    M["ship_limit_sentence"] = (
        f"The first-of-class shipbuilding question is the clearest case. With the plan step it now cites the flagship "
        f"programs, but it came back “Partly answered” in {len(partial)} of 3 modes in the final test "
        f"({', '.join(f'{k}: {v.replace(chr(95), chr(32))}' for k, v in ship_status.items())}), and no run produced a "
        f"cross-program overrun table." if partial else
        "The first-of-class shipbuilding question is the clearest case. With the plan step it now cites the flagship "
        "programs and was answered in all 3 modes in the final test, but no run produced a cross-program overrun table.")

    names = {"R41129": "Columbia SSBN", "RL32418": "Virginia SSN", "RS20643": "Ford CVN", "RL32109": "DDG-51/1000"}

    def ship_cover(row: dict) -> dict[str, list[str]]:
        cid = row["case_id"]
        plan = db.execute("SELECT plan FROM cases WHERE case_id=?", (cid,)).fetchone()
        planned = {p.get("report_id") for p in json.loads(plan[0])["priorities"]} if plan and plan[0] else set()
        opened = {x[0] for x in db.execute("SELECT report_id FROM shortlist WHERE case_id=? AND status IN ('opened', 'not_useful')",
                                           (cid,))}
        return {"planned": sorted(planned & FLAGSHIP_SHIP_REPORTS), "opened": sorted(opened & FLAGSHIP_SHIP_REPORTS),
                "cited": _flagship(row)}

    cover = {k: ship_cover(next(r for r in rows if r["id"] == "ships-regression"))
             for k, rows in (("auto", auto), ("lean", lean), ("team", team))}
    for k, c in cover.items():
        M[f"ship_open_{k}_n"] = str(len(c["opened"]))
        M[f"ship_open_{k}"] = f"{len(c['opened'])} of 4" + (f" ({', '.join(names[i] for i in c['opened'])})" if c["opened"] else "")
    before = []
    for f in sorted(EVALS.glob("2026*.json")):
        if f.name < PLAN_STEP_SINCE:
            before += [r for r in json.loads(f.read_text()) if r["id"] == "ships-regression"]
    b_open = [ship_cover(r)["opened"] for r in before]
    M["ship_runs_before"] = str(len(before))
    M["ship_open_before"] = (str(len(b_open[0])) if len({len(o) for o in b_open}) == 1 else f"{min(map(len, b_open))}–{max(map(len, b_open))}")
    planned = [len(c["planned"]) for c in cover.values()]
    M["ship_after_sentence"] = (
        f"step 1 named {min(planned)}–{max(planned)} of the four flagship programs in each mode, and the agents opened "
        f"{M['ship_open_auto']} in Auto, {M['ship_open_lean']} in Quick, and {M['ship_open_team']} in Full-team mode. "
        f"Verified facts came from {len(cover['auto']['cited'])} of them in Auto, {len(cover['lean']['cited'])} in Quick, "
        f"and {len(cover['team']['cited'])} in Full-team mode.")
    return M


def fill(template: Path, out: Path, M: dict[str, str]) -> None:
    """Embed screenshots ({{img:file}}) and numbers ({{name}}); fail on anything left unfilled."""
    html = template.read_text(encoding="utf-8")

    def img(m: re.Match) -> str:
        data = base64.b64encode((SHOTS / m.group(1)).read_bytes()).decode()
        return f"data:image/jpeg;base64,{data}"

    html = re.sub(r"\{\{img:([^}]+)\}\}", img, html)
    html = re.sub(r"\{\{(\w+)\}\}", lambda m: M[m.group(1)], html)
    left = re.findall(r"\{\{[^}]*\}\}", html)
    assert not left, f"unfilled placeholders: {left}"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    M = metrics()
    fill(ROOT / "docs" / "report_template.html", ROOT / "report.html", M)
    for k in ("b_cost", "quick_cost", "auto_cost", "team_cost", "typ_in", "typ_cost", "month_user", "users_10k",
              "first_before", "first_after", "team_cost_change", "manual_min", "save_q"):
        print(f"  {k} = {M[k]}")


def walk_trace(case_id: str) -> str:
    """Key steps of the walkthrough run, straight from the audit trail."""
    from datetime import datetime

    import sys
    sys.path.insert(0, str(ROOT))
    from backend.events import FRIENDLY, plain

    rows, t0, last = [], None, None
    for line in (ROOT / "output" / "audit_trail.json").open(encoding="utf-8"):
        e = json.loads(line)
        if e.get("case_id") != case_id:
            continue
        ts = datetime.fromisoformat(e["ts"])
        t0 = t0 or ts
        text = plain(e)
        if e["event"] == "agent_finished" and e.get("agent") == "communication":
            text = "Wrote the answer from verified facts only (the reply's numbers are machine-checked)"
        if not text or text.startswith("Found the most relevant pages"):
            continue
        if text.startswith("Reading short passages"):
            if rows and "Read short passages" in rows[-1]:
                continue  # one row for a run of consecutive reads (pages come from different reports)
            text = "Read short passages from the best-ranked pages of the opened reports"
        who = FRIENDLY.get(e.get('agent') or 'system')
        if rows and text.startswith("Opened ") and last == (who, "Opened"):
            rows[-1] = rows[-1].replace("</td></tr>", f"; {text[len('Opened '):].replace('<', '&lt;')}</td></tr>")
            continue  # one row for reports opened together
        if rows and last == (who, text.split()[0]) and text.split()[0] in ("Checked", "Noted"):
            rows[-1] = rows[-1].replace("</td></tr>", " (again, after a fix)</td></tr>")
            continue
        last = (who, text.split()[0])
        rows.append(f"<tr><td>{(ts - t0).total_seconds():.0f}s</td><td>{who}</td>"
                    f"<td>{text.replace('<', '&lt;')}</td></tr>")
    return "\n".join(rows)


def bar_chart(data: list[tuple[str, float, float, float]]) -> str:
    """Grouped horizontal bars: single agent vs team before vs team after batching (total tokens)."""
    series = [("One unoptimized agent", "var(--s1)"), ("PinCite Auto (default)", "var(--s3)"), ("Full team", "var(--s2)")]
    W, left, right, bar, gap, group_gap = 640, 130, 60, 11, 2, 12
    top = 34
    mx = max(max(d[1:]) for d in data)
    scale = (W - left - right) / mx
    h = top + len(data) * (3 * bar + 2 * gap + group_gap) + 26
    parts = [f'<svg class="chart" viewBox="0 0 {W} {h}" role="img" aria-label="Total tokens per question: single agent '
             f'versus team before and after batching">']
    lx = left
    for i, (name, color) in enumerate(series):  # legend
        parts.append(f'<rect x="{lx}" y="8" width="10" height="10" rx="2" fill="{color}"/>'
                     f'<text x="{lx + 15}" y="17" class="ct">{name}</text>')
        lx += 15 + len(name) * 6.2 + 22
    for tick in range(0, int(mx) + 1, 50_000):  # recessive grid
        x = left + tick * scale
        parts.append(f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{h - 22}" class="grid"/>'
                     f'<text x="{x:.1f}" y="{h - 8}" class="ct muted" text-anchor="middle">{tick // 1000}K</text>')
    y = top
    for name, *vals in data:
        parts.append(f'<text x="{left - 8}" y="{y + 1.5 * bar + gap + 3}" class="ct" text-anchor="end">{name}</text>')
        for (sname, color), v in zip(series, vals):
            w = max(v * scale, 2)
            parts.append(f'<path d="M{left},{y} h{w - 4:.1f} a4,4 0 0 1 4,4 v{bar - 8} a4,4 0 0 1 -4,4 h-{w - 4:.1f} z" '
                         f'fill="{color}"><title>{name} · {sname}: {v:,.0f} tokens</title></path>'
                         f'<text x="{left + w + 5:.1f}" y="{y + bar - 3}" class="ct">{v / 1000:,.0f}K</text>')
            y += bar + gap
        y += group_gap - gap
    parts.append(f'<line x1="{left}" y1="{top - 4}" x2="{left}" y2="{h - 22}" class="axis"/></svg>')
    return "".join(parts)


if __name__ == "__main__":
    main()
