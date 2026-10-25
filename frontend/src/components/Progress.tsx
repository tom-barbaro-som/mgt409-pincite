import { useMemo } from 'react'
import { AGENTS, SOLO } from '../agents'
import { timeOf } from '../hooks'
import type { AgentEvent, AgentId, Mode } from '../types'
import { phasesFrom, Workflow } from './Workflow'

export interface TeamState {
  active: AgentId | null           // who is working right now
  finished: Set<AgentId>           // agents that have handed back at least once
  tokens: Partial<Record<AgentId, number>>
  calls: Partial<Record<AgentId, number>>
}

/** Rebuild who-is-doing-what from the audit-trail events (a call stack of delegations). */
export function teamState(events: AgentEvent[]): TeamState {
  const stack: AgentId[] = []
  const finished = new Set<AgentId>()
  const tokens: TeamState['tokens'] = {}
  const calls: TeamState['calls'] = {}
  for (const e of events) {
    const a = e.agent
    if (!a || a === 'system') continue
    if (e.usage) {
      tokens[a] = (tokens[a] ?? 0) + e.usage.input + e.usage.output
      calls[a] = (calls[a] ?? 0) + 1
    }
    if (e.event === 'agent_started') stack.push(a)
    if (e.event === 'agent_finished') {
      finished.add(a)
      const i = stack.lastIndexOf(a)
      if (i >= 0) stack.splice(i, 1)
    }
  }
  return { active: stack[stack.length - 1] ?? null, finished, tokens, calls }
}

export function Progress({ events, elapsed, mode }: { events: AgentEvent[]; elapsed: number; mode: Mode }) {
  const team = useMemo(() => teamState(events), [events])
  const phases = phasesFrom(mode, events, { finished: false, active: team.active })
  const live = phases.find((p) => p.state === 'active')
  const escalated = events.some((e) => e.event === 'escalated')
  const quick = events.some((e) => e.agent === 'solo')
  const steps = mode === 'lean' ? 2 : 3  // numbered like the cards; Full-team mode skips step 2
  const title = live ? `Step ${live.num} of ${steps}: ${live.title}` : 'Starting the research'
  const members = quick ? [SOLO, ...AGENTS] : AGENTS
  const feed = events.filter((e) => e.plain).slice(-7)
  const latest = feed[feed.length - 1]

  return (
    <section className="card progress" aria-live="polite">
      <div className="progress-head">
        <h2><span className="dot" /> {title}</h2>
        <span className="muted small">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} elapsed{quick && !escalated ? ' · usually under 30 seconds' : ' · usually about a minute'}</span>
      </div>

      <Workflow phases={phases} />

      {latest && <p className="now">Now: {latest.plain}</p>}

      <details className="activity">
        <summary>See what each team member is doing</summary>
        <div className="team-row">
          {members.map((a) => {
            const working = team.active === a.id
            const done = team.finished.has(a.id) && !working
            const idle = quick && !escalated && a.id !== 'solo' ? 'Standing by' : 'Waiting'
            return (
              <div key={a.id} className={`member ${working ? 'working' : done ? 'done' : ''}`}>
                <span className="member-name">{a.icon} {a.name}</span>
                <span className="member-state">{working ? 'Working' : done ? 'Done' : idle}</span>
                {team.calls[a.id] ? <span className="member-cost">{team.calls[a.id]} steps</span> : null}
              </div>
            )
          })}
        </div>
        <ol className="feed">
          {feed.map((e) => (
            <li key={e.seq}>
              <span className="feed-time">{timeOf(e.ts)}</span>
              <span className="feed-who">{e.role}</span>
              <span>{e.plain}</span>
            </li>
          ))}
        </ol>
      </details>
    </section>
  )
}
