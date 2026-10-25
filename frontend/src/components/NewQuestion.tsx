import { useEffect, useRef, useState } from 'react'
import { MODES } from '../agents'
import { api, ApiError } from '../api'
import type { Health, Mode } from '../types'
import { phasesForMode, Workflow } from './Workflow'

const EXAMPLES: { topic: string; icon: string; question: string }[] = [
  { topic: 'Health', icon: '🩺', question: 'What impact did passing Obamacare have on the federal budget?' },
  { topic: 'Health', icon: '💊', question: 'What savings are expected from Medicare prescription drug price negotiation?' },
  { topic: 'Education', icon: '🎓', question: 'What do federal student loan forgiveness programs cost?' },
  { topic: 'Infrastructure', icon: '🛣️', question: 'When is the Highway Trust Fund projected to run short of money?' },
  { topic: 'Immigration', icon: '⚖️', question: 'How large is the immigration court backlog, and how has it changed?' },
  { topic: 'Defense', icon: '⚓', question: 'How much have first-of-class Navy ships overrun their cost estimates?' },
]

export function NewQuestion({ health, initial, onStarted, onOpenHelp }: {
  health: Health | null
  initial: string
  onStarted: (caseId: string) => void
  onOpenHelp: () => void
}) {
  const [text, setText] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [mode, setMode] = useState<Mode>('auto')
  const box = useRef<HTMLTextAreaElement>(null)
  const busy = Boolean(health?.running)
  const ready = Boolean(health?.agents_ready)
  const tooShort = text.trim().length < 8

  useEffect(() => {
    box.current?.focus()
    box.current?.setSelectionRange(text.length, text.length)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (tooShort || busy || !ready) return
    setSending(true)
    setError(null)
    try {
      const job = await api.ask(text.trim(), mode)
      onStarted(job.case_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start. Please try again.')
    } finally {
      setSending(false)
    }
  }

  const reports = health?.catalog.reports.toLocaleString() ?? '10,000+'

  return (
    <div className="home">
      <section className="hero">
        <h1>What do you want to know?</h1>
        <p className="hero-sub">
          Ask a policy question in plain English. An AI research team searches {reports} reports from the
          Congressional Research Service and the Government Accountability Office, <b>newest first</b>, and brings back
          exact quotes you can check in seconds.
        </p>
        <div className="ask-box">
          <textarea
            ref={box}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void submit()
              }
            }}
            placeholder="e.g. How has the cost of the Affordable Care Act's premium tax credits changed?"
            rows={3}
            maxLength={1000}
            aria-label="Your question"
          />
          <div className="ask-foot">
            <span className="muted small">
              {busy ? 'The team is finishing another question. You can ask yours when it is done.'
                : mode === 'team' ? 'Press Enter to ask · usually about a minute' : 'Press Enter to ask · often under 30 seconds'}
            </span>
            <button className="primary" onClick={submit} disabled={tooShort || busy || !ready || sending}>
              {sending ? 'Starting…' : 'Find evidence →'}
            </button>
          </div>
        </div>
        <div className="modes" role="radiogroup" aria-label="How thorough should the research be?">
          {MODES.map((m) => (
            <label key={m.id} className={`mode ${mode === m.id ? 'on' : ''}`} title={m.hint}>
              <input type="radio" name="mode" value={m.id} checked={mode === m.id} onChange={() => setMode(m.id)} />
              <span className="mode-label">{m.label}</span>
            </label>
          ))}
          <span className="mode-hint muted small">{MODES.find((m) => m.id === mode)?.hint}</span>
        </div>
        {error && <div className="error">{error}</div>}
        <Workflow phases={phasesForMode(mode)} title={`What happens when you ask (${MODES.find((m) => m.id === mode)?.label.replace(' (recommended)', '')} mode)`} />
      </section>

      <section className="examples">
        <h2>Not sure what to ask? Try one of these</h2>
        <div className="example-grid">
          {EXAMPLES.map((ex) => (
            <button key={ex.question} className="example" onClick={() => { setText(ex.question); box.current?.focus() }}>
              <span className="example-topic">{ex.icon} {ex.topic}</span>
              <span className="example-q">{ex.question}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="steps" aria-label="How it works">
        <div className="step">
          <span className="step-num">1</span>
          <h3>Finds the right reports</h3>
          <p>A search engine built for government reports ranks thousands of them, newest first, so the team reads only the best few.</p>
        </div>
        <div className="step">
          <span className="step-num">2</span>
          <h3>Reads only what matters</h3>
          <p>Instead of reading whole 100-page reports, the team goes straight to the most relevant pages and copies exact quotes.</p>
        </div>
        <div className="step">
          <span className="step-num">3</span>
          <h3>Shows you the proof</h3>
          <p>Every fact is checked against the original PDF and comes with its page number and a highlighted screenshot.</p>
        </div>
      </section>
      <p className="center muted small">
        <button className="link-button" onClick={onOpenHelp}>Learn more about how it works and its limits</button>
      </p>
    </div>
  )
}
