export type AgentId = 'communication' | 'report_search' | 'report_reader' | 'presentation' | 'solo'
export type Mode = 'auto' | 'lean' | 'team'

export interface Health {
  ok: boolean
  model: string
  reasoning_effort: string
  agents_ready: boolean
  case_token_budget: number
  catalog: { dataset: string; reports: number; by_source: Record<string, number>; reports_with_pages_indexed: number }
  running: Job | null
}

export interface Job {
  case_id: string
  request: string
  mode?: Mode
  status: 'running' | 'completed' | 'budget_exceeded' | 'error'
  started_at: string
  finished_at: string | null
  message: string | null
}

export interface CaseListItem {
  case_id: string
  request: string
  created_at: string
  status: string
  headline: string | null
  verified: number
  findings: number
  run_status: Job['status'] | 'interrupted'
  reply_status: string | null
}

export interface Citation {
  text: string
  pdf_link: string | null
  landing_url: string | null
}

export interface BriefFinding {
  finding_id: number
  report_id?: string
  report_date?: string | null
  quote: string
  claim: string
  pdf_page: number
  printed_page: string | null
  evidence_path: string | null
  match_score: number
  relevance: number
  citation: Citation
}

export interface BriefSection {
  heading: string
  summary: string
  findings: BriefFinding[]
}

export interface Brief {
  case_id: string
  request: string
  headline: string
  sections: BriefSection[]
  caveats: string[]
  newest_source_date?: string
  oldest_source_date?: string
}

export interface ShortlistRow {
  report_id: string
  round: number
  rank: number
  why: string
  status: 'proposed' | 'opened' | 'not_useful' | 'unavailable'
  reader_note: string | null
  title: string
  source: string
  pub_date: string
  page_count: number | null
  lane?: string | null
}

export interface ResearchPlan {
  priorities: { priority: string; report_id: string | null; title: string | null; note?: string }[]
  newest_sweep: string[]
}

export interface UsageSummary {
  requests: number
  tool_calls: number
  input_tokens: number
  cache_read_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface ManagerReply {
  status: 'answered' | 'partial' | 'needs_clarification' | 'no_evidence'
  reply: string
  clarifying_question: string | null
  brief_html: string | null
}

export interface RunFile {
  outcome: 'completed' | 'budget_exceeded' | 'error'
  mode?: Mode
  escalated?: boolean
  escalation_reason?: string | null
  reply: ManagerReply | null
  usage: UsageSummary
  usage_by_agent: Partial<Record<AgentId, UsageSummary>>
  delegations: number
  seconds: number
  message: string | null
}

export interface CaseDetail {
  case_id: string
  request: string
  status: string
  budget: { searches: string; reports_opened: string; pages_read: string }
  findings: Record<string, number>
  shortlist: ShortlistRow[]
  plan?: ResearchPlan | null
  brief: Brief | null
  brief_html: string | null
  job: Job | null
  run: RunFile | null
}

export interface AgentEvent {
  seq: number
  ts: string
  event: string
  agent: AgentId | 'system' | null
  agent_label: string
  depth: number | null
  summary: string
  plain: string | null
  role: string
  importance: 'major' | 'minor'
  usage: { input: number; cached_input: number; output: number } | null
  to_agent: AgentId | null
  tool?: string
  priorities?: string[]
  verified?: number
}
