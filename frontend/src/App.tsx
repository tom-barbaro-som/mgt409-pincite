import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { CaseView } from './components/CaseView'
import { History } from './components/History'
import { HowItWorks } from './components/HowItWorks'
import { NewQuestion } from './components/NewQuestion'
import { usePolling } from './hooks'
import type { CaseListItem, Health } from './types'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)
  const [cases, setCases] = useState<CaseListItem[]>([])
  // null = the "new question" page; a #case-… link opens that answer directly (bookmark or share it)
  const [selected, setSelected] = useState<string | null>(() =>
    window.location.hash.startsWith('#case-') ? window.location.hash.slice(1) : null)
  const [draft, setDraft] = useState('')
  const [help, setHelp] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [h, c] = await Promise.all([api.health(), api.cases()])
      setHealth(h)
      setCases(c.cases)
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [])

  usePolling(refresh, health?.running ? 3000 : 10000)

  useEffect(() => {
    const hash = selected ? `#${selected}` : ''
    if (window.location.hash !== hash) window.history.replaceState(null, '', hash || window.location.pathname)
  }, [selected])
  useEffect(() => {  // a pasted or edited #case-… link switches to that answer without a reload
    const onHash = () => setSelected(window.location.hash.startsWith('#case-') ? window.location.hash.slice(1) : null)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const started = (caseId: string) => {
    setDraft('')
    setSelected(caseId)
    void refresh()
  }
  const askAgain = (text: string) => {
    setDraft(text)
    setSelected(null)
  }

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => setSelected(null)} aria-label="Go to the new question page">
          <img src="/favicon.svg" alt="" width={36} height={36} />
          <span>
            <span className="brand-name">PinCite</span>
            <span className="brand-sub">Page-cited answers from official U.S. government reports</span>
          </span>
        </button>
        <div className="top-actions">
          {health && (
            <span className={`status ${health.agents_ready ? 'ok' : 'warn'}`} title={`${health.model} · ${health.catalog.dataset} dataset`}>
              <span className="dot" /> {health.agents_ready ? (health.running ? 'Working on a question' : 'Ready') : 'API key missing'}
            </span>
          )}
          <button className="link-button" onClick={() => setHelp(true)}>How it works</button>
        </div>
      </header>

      {offline && (
        <div className="banner">
          The app can't reach its server. Start it with <code>uvicorn backend.main:app --port 8010</code> in the project folder, then refresh.
        </div>
      )}
      {health && !health.agents_ready && (
        <div className="banner warn">
          Asking questions needs an API key. Add <code>PORTKEY_API_KEY</code> to a <code>.env</code> file (see the README) and restart the server. You can still browse past answers.
        </div>
      )}

      <div className="layout">
        <aside className="sidebar">
          <button className="new-question" onClick={() => askAgain('')}>＋ New question</button>
          <History cases={cases} selected={selected} onSelect={setSelected} />
        </aside>
        <main className="main">
          {selected ? (
            <CaseView key={selected} caseId={selected} onFinished={refresh} onAskAgain={askAgain} onStarted={started} />
          ) : (
            <NewQuestion key={draft} health={health} initial={draft} onStarted={started} onOpenHelp={() => setHelp(true)} />
          )}
        </main>
      </div>

      {help && <HowItWorks health={health} onClose={() => setHelp(false)} />}
    </div>
  )
}
