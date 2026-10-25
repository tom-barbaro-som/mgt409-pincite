import { useEffect } from 'react'
import { AGENTS, SOLO } from '../agents'
import type { Health } from '../types'
import { phasesForMode, Workflow } from './Workflow'

export function HowItWorks({ health, onClose }: { health: Health | null; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="how-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 id="how-title">How PinCite works</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <h3>Where the answers come from</h3>
        <p>
          The <b>Congressional Research Service (CRS)</b> writes nonpartisan reports for Congress, and the{' '}
          <b>Government Accountability Office (GAO)</b> audits federal programs. Their reports are trusted but long and hard
          to search. This app searches {health ? health.catalog.reports.toLocaleString() : 'thousands of'} of them.
        </p>

        <h3>How a question moves through the team</h3>
        <p>
          In <b>Auto</b> mode (the default) a question goes through up to three steps:
        </p>
        <ol>
          <li><b>Plan.</b> The Solo researcher decides what matters most for the question (for example, the largest
            programs, such as the Navy's submarine, carrier, and destroyer programs for a shipbuilding-cost question) and
            asks the search engine for the best report on each, plus the newest reports overall.</li>
          <li><b>Quick pass.</b> The same agent reads the best pages and copies exact quotes. Code then checks every quote
            against its PDF page, checks every number, and takes the highlighted screenshots.</li>
          <li><b>Full team, only if needed.</b> If fewer than two facts verify, the four-agent team takes over with the plan
            and findings already in hand, and an AI fact-checker re-checks each quote in context.</li>
        </ol>
        <Workflow phases={phasesForMode('auto')} compact />
        <p>
          <b>Quick</b> mode stops after step 2. <b>Full team</b> mode skips the quick pass. For a partial answer you can always
          click <b>Dig deeper with the full team</b>.
        </p>
        <div className="team-grid">
          {[SOLO, ...AGENTS].map((a) => (
            <div key={a.id} className="team-card">
              <div className="team-name">{a.icon} {a.name}</div>
              <div className="small">{a.does}</div>
            </div>
          ))}
        </div>

        <h3>What matters most, newest information first</h3>
        <p>
          The plan makes sure the biggest items get covered, not just whatever was published last. Within that, reports are
          searched from newest to oldest. Older reports are used only after newer ones are exhausted or when
          they hold history that newer ones lack. Every fact shows its report's date, and answers built only on older
          sources say so.
        </p>

        <h3>Why you can trust (and check) every fact</h3>
        <ul>
          <li>Each fact is an <b>exact quote</b>. The app refuses quotes that don't match the PDF page word for word.</li>
          <li>Numbers in a summary must appear in the quoted text, or the answer is sent back for a fix.</li>
          <li>Every fact links to the PDF <b>at the exact page</b>, with a highlighted screenshot.</li>
        </ul>

        <h3>Limits to know about</h3>
        <ul>
          <li>Only reports in this catalog are searched. Agencies like CBO aren't included directly, though CRS often quotes them.</li>
          <li>GAO blocks automated downloads, so a GAO report's full text is only available if someone adds its PDF.</li>
          <li>Reports often contain <i>estimates</i> and projections. The answer labels them as such.</li>
        </ul>

        {health && (
          <p className="muted small">
            Model: {health.model} · Dataset: {health.catalog.dataset} ({health.catalog.reports.toLocaleString()} reports) ·
            Each question has a {Math.round(health.case_token_budget / 1000)}k-token budget and hard limits on searches and pages read.
          </p>
        )}
      </div>
    </div>
  )
}
