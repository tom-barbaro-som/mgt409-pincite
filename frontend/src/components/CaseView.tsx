import { useRef, useState } from 'react'
import { api, ApiError, fileUrl } from '../api'
import { relativeTime, useElapsed, usePolling } from '../hooks'
import type { AgentEvent, CaseDetail } from '../types'
import { AnswerView } from './AnswerView'
import { BehindTheScenes } from './BehindTheScenes'
import { Progress, teamState } from './Progress'
import { phasesFrom, Workflow } from './Workflow'

export function CaseView({ caseId, onFinished, onAskAgain, onStarted }: {
  caseId: string
  onFinished: () => void
  onAskAgain: (text: string) => void
  onStarted: (caseId: string) => void
}) {
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [tab, setTab] = useState<'answer' | 'behind'>('answer')
  const [error, setError] = useState<string | null>(null)
  const lastSeq = useRef(0)
  const wasRunning = useRef(false)

  const running = detail?.job?.status === 'running'
  const startedAt = detail?.job?.started_at ?? events[0]?.ts
  const elapsed = useElapsed(startedAt, running)

  usePolling(async () => {
    try {
      const d = await api.case(caseId)
      setDetail(d)
      setError(null)
      const nowRunning = d.job?.status === 'running'
      if (wasRunning.current && !nowRunning) onFinished()
      wasRunning.current = nowRunning
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load this question.')
    }
  }, running || !detail ? 2000 : 15000, true, caseId)

  usePolling(async () => {
    const res = await api.events(caseId, lastSeq.current)
    if (res.events.length) {
      lastSeq.current = res.last_seq
      setEvents((prev) => [...prev, ...res.events])
    }
  }, running || !detail ? 1000 : 20000, true, caseId)

  if (error && !detail) return <div className="card error">{error}</div>
  if (!detail) return <div className="card muted">Loading…</div>

  const reply = detail.run?.reply ?? null
  const failed = !running && !reply
  const quick = Boolean(detail.run?.usage_by_agent?.solo) && !detail.run?.escalated
  const mode = detail.job?.mode ?? detail.run?.mode ?? (detail.run?.usage_by_agent?.solo ? 'auto' : 'team')
  const deeper = async () => {
    try {
      const job = await api.ask(detail.request, 'team')
      onStarted(job.case_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the full team.')
    }
  }

  return (
    <div className="case">
      <header className="question-head">
        <div>
          <div className="eyebrow">Your question · asked {relativeTime(startedAt ?? new Date().toISOString())}</div>
          <h1>{detail.request}</h1>
        </div>
        {detail.brief_html && (
          <a className="button ghost" href={fileUrl(detail.brief_html)} target="_blank" rel="noreferrer"
             title="A single file with every quote and screenshot, for printing or sharing">
            ⬇ Printable report
          </a>
        )}
      </header>

      {running && <Progress events={events} elapsed={elapsed} mode={mode} />}

      {!running && reply && (
        <Workflow compact title="How this answer was made"
          phases={phasesFrom(mode, events, { finished: true, escalated: detail.run?.escalated,
            verified: detail.findings?.verified ?? 0, plan: detail.plan, active: teamState(events).active,
            replyStatus: reply.status })} />
      )}

      {error && detail && <div className="card error">{error}</div>}
      {failed && (
        <section className="card s-none">
          <p><b>This question didn't finish.</b> {detail.job?.message ?? detail.run?.message ?? 'The run was interrupted.'}</p>
          <button className="primary" onClick={() => onAskAgain(detail.request)}>Ask it again</button>
        </section>
      )}

      {!running && (
        <>
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'answer'} className={tab === 'answer' ? 'active' : ''} onClick={() => setTab('answer')}>
              Answer &amp; evidence
            </button>
            <button role="tab" aria-selected={tab === 'behind'} className={tab === 'behind' ? 'active' : ''} onClick={() => setTab('behind')}>
              Behind the scenes
            </button>
          </div>
          {tab === 'answer'
            ? <AnswerView brief={detail.brief} reply={reply} request={detail.request} onAskAgain={onAskAgain}
                onDeeper={deeper} quick={quick} />
            : <BehindTheScenes events={events} detail={detail} />}
        </>
      )}
    </div>
  )
}
