import { useEffect, useRef, useState } from 'react'

/** Calls `fn` now and every `ms` while `enabled`; the latest `fn` is always used. */
export function usePolling(fn: () => void | Promise<void>, ms: number, enabled = true, key: unknown = null) {
  const saved = useRef(fn)
  useEffect(() => {
    saved.current = fn
  })
  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer = 0
    const tick = async () => {
      try {
        await saved.current()
      } catch {
        /* errors are surfaced by the caller's own state */
      }
      if (!stopped) timer = window.setTimeout(tick, ms)
    }
    timer = window.setTimeout(tick, 0)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [ms, enabled, key])
}

/** Seconds since `iso`, re-rendering every second while `running`. */
export function useElapsed(iso: string | undefined, running: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [running])
  return iso ? Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000)) : 0
}

export const num = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString('en-US'))

export const timeOf = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })

export const longDate = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : 'undated'

export const yearsOld = (iso: string | null | undefined) =>
  iso ? (Date.now() - new Date(`${iso.slice(0, 10)}T12:00:00`).getTime()) / (365.25 * 24 * 3600 * 1000) : null

export const ageLabel = (iso: string | null | undefined) => {
  const y = yearsOld(iso)
  if (y == null) return 'undated'
  if (y < 1 / 12) return 'this month'
  if (y < 1) {
    const m = Math.floor(y * 12)
    return `${m} month${m === 1 ? '' : 's'} old`
  }
  const n = Math.floor(y)
  return `${n} year${n === 1 ? '' : 's'} old`
}

export const relativeTime = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} hr ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
