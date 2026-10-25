import { relativeTime } from '../hooks'
import type { CaseListItem } from '../types'

const STATUS: Record<string, { icon: string; label: string }> = {
  running: { icon: '⏳', label: 'Working…' },
  answered: { icon: '✓', label: 'Answered' },
  partial: { icon: '◐', label: 'Partly answered' },
  no_evidence: { icon: '∅', label: 'No evidence found' },
  needs_clarification: { icon: '?', label: 'Needs more detail' },
  error: { icon: '!', label: 'Something went wrong' },
  budget_exceeded: { icon: '!', label: 'Stopped (budget)' },
  interrupted: { icon: '·', label: 'Interrupted' },
}

export function History({ cases, selected, onSelect }: {
  cases: CaseListItem[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  return (
    <nav className="history" aria-label="Your questions">
      <h2>Your questions</h2>
      {cases.length === 0 && <p className="muted small">Questions you ask will appear here.</p>}
      <ul>
        {cases.map((c) => {
          const key = c.run_status === 'running' ? 'running' : c.reply_status ?? c.run_status
          const st = STATUS[key] ?? STATUS.interrupted
          return (
            <li key={c.case_id}>
              <button className={`history-row ${selected === c.case_id ? 'active' : ''}`} onClick={() => onSelect(c.case_id)}>
                <span className="history-q">{c.request}</span>
                <span className={`history-meta k-${key}`}>
                  <span className="history-icon" aria-hidden>{st.icon}</span> {st.label}
                  {c.verified > 0 && ` · ${c.verified} fact${c.verified === 1 ? '' : 's'}`}
                  <span className="history-time">{relativeTime(c.created_at)}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
