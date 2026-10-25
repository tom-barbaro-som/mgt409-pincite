import { useMemo, useState } from 'react'
import { AGENTS, SOLO } from '../agents'
import { longDate, num, timeOf } from '../hooks'
import type { AgentEvent, CaseDetail } from '../types'
import { teamState } from './Progress'

const SHORTLIST_LABEL: Record<string, string> = {
  proposed: 'Not needed',
  opened: 'Read',
  not_useful: 'Not useful',
  unavailable: 'PDF unavailable',
}

function Meter({ label, value, hint }: { label: string; value: string; hint: string }) {
  const [used, max] = value.split('/').map(Number)
  const pct = max ? Math.min(100, (used / max) * 100) : 0
  return (
    <div className="meter" title={hint}>
      <div className="meter-top"><span>{label}</span><b>{used} of {max}</b></div>
      <div className="meter-bar"><span style={{ width: `${pct}%` }} /></div>
      <div className="meter-hint">{hint}</div>
    </div>
  )
}

export function BehindTheScenes({ events, detail }: { events: AgentEvent[]; detail: CaseDetail }) {
  const [all, setAll] = useState(false)
  const team = useMemo(() => teamState(events), [events])
  const byAgent = detail.run?.usage_by_agent
  const total = detail.run?.usage
  const shown = all ? events : events.filter((e) => e.importance === 'major')
  const run = detail.run
  const usedSolo = Boolean(byAgent?.solo) || events.some((e) => e.agent === 'solo')
  const usedTeam = AGENTS.some((a) => byAgent?.[a.id]) || events.some((e) => e.agent === 'communication')
  const members = [...(usedSolo ? [SOLO] : []), ...(usedTeam ? AGENTS : [])]
  const path = run?.escalated
    ? `Auto mode: plan and quick pass first, then the full team took over (${run.escalation_reason?.replace(/\.$/, '').toLowerCase()}).`
    : usedSolo ? `${run?.mode === 'lean' ? 'Quick' : 'Auto'} mode: answered by the plan and quick pass; code checked every quote.`
      : 'Full-team mode: all four agents, with an AI fact-checker.'

  return (
    <>
      <section className="card">
        <h2>How much work it took</h2>
        <p className="path">{path}</p>
        <p className="muted small">
          The team stays inside hard limits, so it never reads more than it needs. Ranking happens in code at no cost; the AI reads only short passages.
        </p>
        <div className="meters">
          <Meter label="Searches" value={detail.budget.searches} hint="Catalog searches the Researcher ran" />
          <Meter label="Reports opened" value={detail.budget.reports_opened} hint="Reports the Reader actually opened" />
          <Meter label="Pages read" value={detail.budget.pages_read} hint="Pages whose passages the AI read" />
        </div>
      </section>

      {detail.plan && detail.plan.priorities.length > 0 && (
        <section className="card">
          <h2>Research plan</h2>
          <p className="muted small">
            Step 1 picked what matters most for this question, and the search engine found the best report for each,
            alongside a sweep of the newest reports.
          </p>
          <ol className="plan-list">
            {detail.plan.priorities.map((p) => (
              <li key={p.priority}>
                <b>{p.priority}</b>
                <span className="muted small"> → {p.title ? `${p.report_id}: ${p.title}` : (p.note ?? 'no report found')}</span>
              </li>
            ))}
            {detail.plan.newest_sweep.length > 0 && (
              <li><b>Newest reports</b><span className="muted small"> → {detail.plan.newest_sweep.join(', ')}</span></li>
            )}
          </ol>
        </section>
      )}

      <section className="card">
        <h2>Reports the team considered</h2>
        <p className="muted small">Picked from the research plan: a report for each priority, plus the newest coverage.</p>
        {detail.shortlist.length === 0 && <p className="muted">No reports were needed.</p>}
        <ol className="shortlist">
          {detail.shortlist.map((r) => (
            <li key={r.report_id}>
              <div className="sl-top">
                <span className="sl-title">{r.title}</span>
                <span className="sl-tags">
                  {r.lane && <span className={`tag lane ${r.lane.startsWith('newest') ? 'lane-new' : 'lane-pri'}`}
                    title={r.lane}>{r.lane.startsWith('newest') ? 'Newest' : 'Priority'}</span>}
                  <span className={`tag t-${r.status}`}>{SHORTLIST_LABEL[r.status]}</span>
                </span>
              </div>
              <div className="muted small">
                {r.source} {r.report_id} · {longDate(r.pub_date)}{r.page_count ? ` · ${r.page_count} pages` : ''} · Why: {r.why}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="card">
        <h2>AI usage</h2>
        <table className="table">
          <thead><tr><th>Team member</th><th>AI calls</th><th>Input tokens</th><th>…of which cached</th><th>Output tokens</th></tr></thead>
          <tbody>
            {members.map((a) => {
              const u = byAgent?.[a.id]
              return (
                <tr key={a.id}>
                  <td>{a.icon} {a.name} <span className="muted small">({a.role})</span></td>
                  <td>{u?.requests ?? team.calls[a.id] ?? 0}</td>
                  <td>{num(u?.input_tokens)}</td>
                  <td>{num(u?.cache_read_tokens)}</td>
                  <td>{num(u?.output_tokens)}</td>
                </tr>
              )
            })}
          </tbody>
          {total && (
            <tfoot>
              <tr><td>Total · {detail.run?.seconds}s</td><td>{total.requests}</td><td>{num(total.input_tokens)}</td>
                <td>{num(total.cache_read_tokens)}</td><td>{num(total.output_tokens)}</td></tr>
            </tfoot>
          )}
        </table>
      </section>

      <section className="card">
        <div className="section-title">
          <h2>Full activity log</h2>
          <label className="small muted toggle">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show every step ({events.length})
          </label>
        </div>
        <p className="muted small">Every step is recorded in <code>output/audit_trail.json</code>.</p>
        <ol className="feed log">
          {shown.map((e) => (
            <li key={e.seq} className={`${e.importance} ev-${e.event}`} style={{ paddingLeft: `${(e.depth ?? 0) * 14 + 8}px` }}>
              <span className="feed-time">{timeOf(e.ts)}</span>
              <span className="feed-who">{e.role}</span>
              <span>{e.plain ?? e.summary}</span>
            </li>
          ))}
        </ol>
      </section>
    </>
  )
}
