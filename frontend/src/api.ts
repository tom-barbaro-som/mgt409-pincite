import type { AgentEvent, CaseDetail, CaseListItem, Health, Job, Mode } from './types'

// Relative URLs: Vite proxies them in development; FastAPI serves them in production.
export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body?.detail
    const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail[0]?.msg : `Request failed (${res.status})`
    throw new ApiError(msg ?? `Request failed (${res.status})`)
  }
  return body as T
}

export const api = {
  health: () => call<Health>('/api/health'),
  cases: () => call<{ cases: CaseListItem[] }>('/api/cases'),
  case: (id: string) => call<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  ask: (request: string, mode: Mode) => call<Job>('/api/cases', { method: 'POST', body: JSON.stringify({ request, mode }) }),
  events: (caseId: string, after: number) =>
    call<{ last_seq: number; events: AgentEvent[] }>(`/api/events?case_id=${encodeURIComponent(caseId)}&after=${after}`),
}

/** Output files are stored as project-relative paths like output/evidence/<case>/finding_1.png. */
export const fileUrl = (path: string | null | undefined) =>
  path ? `/files/${path.replace(/^output\//, '')}` : undefined

export const pdfUrl = (reportId: string, page: number) => `/api/reports/${encodeURIComponent(reportId)}/pdf#page=${page}`
