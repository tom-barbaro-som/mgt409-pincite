import type { ReactNode } from 'react'

/** Tiny, safe Markdown subset for agent replies: paragraphs, "- " bullets, **bold**, and [links](url).
 *  Builds React nodes (no innerHTML), so model output can never inject markup. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    if (m[1]) out.push(<strong key={`${key}-b${i++}`}>{m[1]}</strong>)
    else {
      const href = /^(https?:\/\/|\/|output\/)/.test(m[3]) ? m[3].replace(/^output\//, '/files/') : undefined
      out.push(href ? <a key={`${key}-a${i++}`} href={href} target="_blank" rel="noreferrer">{m[2]}</a> : m[2])
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\s*\n/)
  return (
    <div className="md">
      {blocks.map((block, bi) => {
        const lines = block.split('\n').filter((l) => l.trim())
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l))) {
          return (
            <ul key={bi}>
              {lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*([-*]|\d+\.)\s/, ''), `${bi}-${li}`)}</li>)}
            </ul>
          )
        }
        return <p key={bi}>{inline(lines.join(' '), `${bi}`)}</p>
      })}
    </div>
  )
}
