import type { AgentEvent, AgentId, Mode, ResearchPlan } from '../types'

/**
 * The research workflow, drawn the same way everywhere (home page, live progress, finished answer):
 *   1 Plan  →  2 Quick pass  →  [only if fewer than 2 facts verify]  3 Full team  →  Answer
 */
export type PhaseState = 'todo' | 'active' | 'done' | 'skipped' | 'off'
export interface PhaseView { key: 'plan' | 'quick' | 'team'; num: number; title: string; who: string; what: string; state: PhaseState; note?: string }

const TEAM_STEP: Partial<Record<AgentId, string>> = {
  communication: 'Coordinator is planning and judging the results',
  report_search: 'Researcher is finding reports for the gaps',
  report_reader: 'Reader is copying exact quotes from the best pages',
  presentation: 'AI fact-checker is re-checking each quote in context',
}

export const MIN_VERIFIED = 2

function base(mode: Mode): PhaseView[] {
  return [
    { key: 'plan', num: 1, title: 'Plan', who: mode === 'team' ? 'Coordinator' : 'Solo researcher', state: 'todo',
      what: 'Picks what matters most (e.g. the largest programs) and sweeps the newest reports' },
    { key: 'quick', num: 2, title: 'Quick pass', who: 'Solo researcher + code', state: mode === 'team' ? 'off' : 'todo',
      what: 'Reads the best pages and quotes them; code checks every quote and takes screenshots',
      note: mode === 'team' ? 'Skipped in Full-team mode' : undefined },
    { key: 'team', num: 3, title: 'Full team', who: 'Coordinator · Researcher · Reader · AI fact-checker',
      state: mode === 'lean' ? 'off' : 'todo',
      what: mode === 'team' ? 'Searches, reads, and fact-checks with four agents'
        : mode === 'lean' ? 'Not used in Quick mode'
          : `Only if fewer than ${MIN_VERIFIED} facts verify: fills the gaps, with an AI fact-checker`,
      note: mode === 'lean' ? 'Off in Quick mode (use “Dig deeper”)' : undefined },
  ]
}

/** The workflow before anything has run (home page, How it works). */
export const phasesForMode = (mode: Mode): PhaseView[] => base(mode)

const shortName = (p: string) => p.replace(/\s*\(.*?\)\s*/g, ' ').trim()

/** Phase states rebuilt from the audit-trail events (live) or from the finished run. */
export function phasesFrom(mode: Mode, events: AgentEvent[], opts: {
  finished: boolean; escalated?: boolean; verified?: number; plan?: ResearchPlan | null; active?: AgentId | null
  replyStatus?: string | null
}): PhaseView[] {
  const p = base(mode)
  const [plan, quick, team] = p
  const searched = events.some((e) => e.event === 'tool_result' && e.tool === 'search_reports')
  const planEvent = events.find((e) => e.priorities?.length)
  const priorities = planEvent?.priorities ?? opts.plan?.priorities.map((x) => x.priority) ?? []
  const quickDone = events.some((e) => e.event === 'auto_verified' || (e.event === 'agent_finished' && e.agent === 'solo'))
  const checks = events.filter((e) => e.event === 'auto_verified')
  // Code may check twice (the agent gets one chance to re-record near-miss quotes): add up every check.
  const quickVerified = checks.length ? checks.reduce((sum, e) => sum + (e.verified ?? 0), 0) : undefined
  const escalated = opts.escalated || events.some((e) => e.event === 'escalated')
  const soloWorking = events.some((e) => e.agent === 'solo')

  // 1. Plan
  if (searched || (opts.finished && opts.replyStatus !== 'needs_clarification')) {
    plan.state = 'done'
    plan.note = priorities.length ? `Priorities: ${priorities.map(shortName).join(' · ')} + the newest reports`
      : 'Searched the newest reports first'
  } else if (opts.finished) {
    plan.state = 'skipped'
    plan.note = 'Asked you for more detail first'
  } else if (soloWorking || events.some((e) => e.agent === 'communication')) {
    plan.state = 'active'
    plan.note = 'Deciding what matters most for this question…'
  }

  // 2. Quick pass
  if (mode !== 'team') {
    const n = opts.finished && !escalated && opts.verified !== undefined ? opts.verified : quickVerified
    if (quickDone || (opts.finished && plan.state === 'done')) {
      quick.state = 'done'
      quick.note = n !== undefined ? `${n} fact${n === 1 ? '' : 's'} verified by code` : 'Finished'
    } else if (plan.state === 'done') {
      quick.state = 'active'
      quick.note = 'Reading the best pages and copying exact quotes…'
    } else if (plan.state === 'skipped') {
      quick.state = 'skipped'
    }
  }

  // 3. Full team
  if (mode === 'team') {
    team.state = opts.finished ? 'done' : 'active'
    team.note = opts.finished ? `${opts.verified ?? 0} verified facts` : (opts.active && TEAM_STEP[opts.active]) || 'Starting…'
  } else if (mode === 'auto') {
    if (escalated) {
      team.state = opts.finished ? 'done' : 'active'
      team.note = opts.finished ? `Filled the gaps: ${opts.verified ?? 0} verified facts in total`
        : (opts.active && TEAM_STEP[opts.active]) || 'Taking over…'
    } else if (opts.finished || quickDone) {
      team.state = 'skipped'
      team.note = plan.state === 'skipped' ? 'Not needed yet' : `Not needed: ${opts.verified ?? quickVerified ?? 0} facts verified`
    }
  }
  return p
}

export function Workflow({ phases, title, compact = false }: { phases: PhaseView[]; title?: string; compact?: boolean }) {
  const done = phases.every((x) => x.state !== 'active' && x.state !== 'todo')
  return (
    <div className={`flow-wrap ${compact ? 'compact' : ''}`}>
      {title && <div className="flow-title">{title}</div>}
      <ol className="flow" aria-label="Research workflow">
        {phases.map((ph, i) => (
          <li key={ph.key} className="flow-item">
            {i > 0 && (
              <span className={`flow-arrow ${ph.key === 'team' ? 'gate' : ''}`} aria-hidden>
                {ph.key === 'team' && phases[1].state !== 'off' && ph.state !== 'off'
                  ? <span className="gate-label">fewer than {MIN_VERIFIED} verified?</span> : null}
                →
              </span>
            )}
            <div className={`phase ${ph.state}`}>
              <div className="phase-top">
                <span className="phase-num" aria-hidden>{ph.state === 'done' ? '✓' : ph.num}</span>
                <span className="phase-title">{ph.title}</span>
                {ph.state === 'active' && <span className="phase-live">now</span>}
              </div>
              <div className="phase-who">{ph.who}</div>
              {!compact && <div className="phase-what">{ph.what}</div>}
              {ph.note && <div className="phase-note">{ph.note}</div>}
            </div>
          </li>
        ))}
        <li className="flow-item">
          <span className="flow-arrow" aria-hidden>→</span>
          <div className={`phase answer ${done ? 'done' : 'todo'}`}>
            <div className="phase-top"><span className="phase-num" aria-hidden>{done ? '✓' : '★'}</span><span className="phase-title">Answer</span></div>
            <div className="phase-who">verified facts only</div>
            {!compact && <div className="phase-what">Each with its exact quote, PDF page, and highlighted screenshot</div>}
          </div>
        </li>
      </ol>
    </div>
  )
}
