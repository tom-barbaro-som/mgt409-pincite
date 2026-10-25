import { useState } from 'react'
import { SOURCE_NAMES } from '../agents'
import { fileUrl, pdfUrl } from '../api'
import { ageLabel, longDate, yearsOld } from '../hooks'
import type { Brief, BriefFinding, ManagerReply } from '../types'
import { Markdown } from './Markdown'

const STATUS = {
  answered: { cls: 'answered', icon: '✓', title: 'Answered' },
  partial: { cls: 'partial', icon: '◐', title: 'Partly answered' },
  no_evidence: { cls: 'none', icon: '∅', title: "We couldn't find evidence for this" },
  needs_clarification: { cls: 'clarify', icon: '?', title: 'Can you say a bit more?' },
} as const

const reportIdOf = (f: BriefFinding) => f.report_id ?? f.citation.text.match(/^\S+ ([^,\s]+),/)?.[1] ?? ''
const sourceOf = (f: BriefFinding) => f.citation.text.split(' ')[0]
const titleOf = (f: BriefFinding) => f.citation.text.match(/"([^"]+)"/)?.[1] ?? ''
const dateOfFinding = (f: BriefFinding) => f.report_date ?? f.citation.text.match(/\((\d{4}-\d{2}-\d{2})\)/)?.[1] ?? null

function EvidenceCard({ f, n, onZoom }: { f: BriefFinding; n: number; onZoom: (src: string, caption: string) => void }) {
  const [copied, setCopied] = useState(false)
  const shot = fileUrl(f.evidence_path)
  const reportId = reportIdOf(f)
  const d = dateOfFinding(f)
  const old = (yearsOld(d) ?? 0) > 3
  const page = f.printed_page && f.printed_page !== String(f.pdf_page) ? `page ${f.pdf_page} (printed page ${f.printed_page})` : `page ${f.pdf_page}`
  const source = sourceOf(f)
  const cite = `${SOURCE_NAMES[source] ?? source}, "${titleOf(f)}," ${reportId}, ${longDate(d)}, ${page.replace('page', 'p.')}.`

  const copy = async () => {
    await navigator.clipboard.writeText(`"${f.quote}" ${cite}`)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <article className="evidence">
      <div className="evidence-main">
        <div className="evidence-top">
          <span className="fact-num">Fact {n}</span>
          <span className={`age ${old ? 'old' : 'fresh'}`} title={`Report dated ${longDate(d)}`}>
            {old ? '⚠ ' : ''}{longDate(d)} · {ageLabel(d)}
          </span>
        </div>
        <p className="claim">{f.claim}</p>
        <blockquote>“{f.quote}”</blockquote>
        <p className="source">
          <b>{titleOf(f)}</b>
          <br />
          <span title={SOURCE_NAMES[source]}>{SOURCE_NAMES[source] ?? source}</span> report {reportId} · {page}
        </p>
        <div className="evidence-actions">
          {shot && <button className="button" onClick={() => onZoom(shot, `${titleOf(f)}, ${page}`)}>View highlighted page</button>}
          {reportId && <a className="button ghost" href={pdfUrl(reportId, f.pdf_page)} target="_blank" rel="noreferrer">Open PDF at {page.split(' (')[0]} ↗</a>}
          <button className="button ghost" onClick={copy}>{copied ? 'Copied ✓' : 'Copy quote + citation'}</button>
        </div>
      </div>
      {shot && (
        <button className="thumb" onClick={() => onZoom(shot, `${titleOf(f)}, ${page}`)} aria-label="Enlarge the highlighted page">
          <img src={shot} alt={`The quote highlighted on ${page} of report ${reportId}`} loading="lazy" />
          <span className="thumb-cap">🔍 Quote highlighted on {page.split(' (')[0]}</span>
        </button>
      )}
    </article>
  )
}

export function AnswerView({ brief, reply, request, onAskAgain, onDeeper, quick }: {
  brief: Brief | null
  reply: ManagerReply | null
  request: string
  onAskAgain: (text: string) => void
  onDeeper: () => void
  quick: boolean
}) {
  const [zoom, setZoom] = useState<{ src: string; caption: string } | null>(null)
  const st = reply ? STATUS[reply.status] : null
  const facts = brief?.sections.flatMap((s) => s.findings) ?? []
  const reports = new Set(facts.map(reportIdOf))
  const newestOld = (yearsOld(brief?.newest_source_date) ?? 0) > 3
  let n = 0

  return (
    <>
      {reply && st && (
        <section className={`card answer s-${st.cls}`}>
          <div className="answer-status"><span className="answer-icon" aria-hidden>{st.icon}</span> {st.title}</div>
          {reply.status === 'needs_clarification' ? (
            <>
              <p className="clarify-q">{reply.clarifying_question ?? reply.reply}</p>
              <button className="primary" onClick={() => onAskAgain(`${request} `)}>Add detail and ask again</button>
            </>
          ) : (
            <Markdown text={reply.reply.replace(/See the \[[^\]]*\]\([^)]*\)\.?/gi, '').replace(/\s*See the (cited|full) brief[^.]*\./gi, '')} />
          )}
          {reply.status === 'no_evidence' && (
            <button className="primary" onClick={() => onAskAgain(request)}>Try rewording the question</button>
          )}
          {quick && (reply.status === 'partial' || reply.status === 'no_evidence') && (
            <div className="deeper">
              <span>This came from the quick one-agent pass. The full team reads more reports and has an AI fact-checker (about a minute).</span>
              <button className="button" onClick={onDeeper}>Dig deeper with the full team</button>
            </div>
          )}
          {facts.length > 0 && (
            <p className={`freshness ${newestOld ? 'old' : ''}`}>
              Based on {facts.length} verified fact{facts.length === 1 ? '' : 's'} from {reports.size} report{reports.size === 1 ? '' : 's'}{' '}
              published {brief?.oldest_source_date?.slice(0, 4)}
              {brief?.newest_source_date?.slice(0, 4) !== brief?.oldest_source_date?.slice(0, 4) && `–${brief?.newest_source_date?.slice(0, 4)}`}.
              {newestOld && ` The newest source is ${ageLabel(brief?.newest_source_date)}; more recent figures may exist outside these reports.`}
            </p>
          )}
        </section>
      )}

      {brief && (
        <section className="evidence-list">
          <div className="section-title">
            <h2>The evidence</h2>
            <span className="muted small">Newest first · every quote was matched word-for-word to its PDF page</span>
          </div>
          {brief.sections.map((s) => (
            <div key={s.heading} className="theme">
              <h3>{s.heading}</h3>
              <p className="theme-summary">{s.summary}</p>
              {s.findings.map((f) => <EvidenceCard key={f.finding_id} f={f} n={++n} onZoom={(src, caption) => setZoom({ src, caption })} />)}
            </div>
          ))}
          {brief.caveats.length > 0 && (
            <div className="caveats">
              <h3>Keep in mind</h3>
              <ul>{brief.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          )}
        </section>
      )}

      {zoom && (
        <div className="modal-backdrop" onClick={() => setZoom(null)}>
          <figure className="lightbox" role="dialog" aria-label="Highlighted source page" onClick={(e) => e.stopPropagation()}>
            <img src={zoom.src} alt={zoom.caption} />
            <figcaption>
              <span>{zoom.caption} · the yellow highlight is the exact quote</span>
              <button className="button" onClick={() => setZoom(null)}>Close</button>
            </figcaption>
          </figure>
        </div>
      )}
    </>
  )
}
