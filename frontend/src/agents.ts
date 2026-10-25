import type { AgentId } from './types'

/** The four agents, described in plain language for people new to the app. */
export const AGENTS: { id: AgentId; name: string; role: string; does: string; icon: string }[] = [
  { id: 'communication', name: 'Coordinator', role: 'Communication agent', icon: '🧭',
    does: 'Understands your question, directs the team, and writes the answer.' },
  { id: 'report_search', name: 'Researcher', role: 'Report Search agent', icon: '🔎',
    does: 'Searches 10,000+ reports: the newest first, plus the best report on each priority program, and picks a few.' },
  { id: 'report_reader', name: 'Reader', role: 'Report Reader agent', icon: '📖',
    does: 'Reads only the most relevant pages and copies exact quotes.' },
  { id: 'presentation', name: 'Fact-checker', role: 'Presentation agent', icon: '✅',
    does: 'Checks every quote against the PDF and captures a highlighted screenshot.' },
]

/** Steps 1-2 of Auto (and all of Quick): one agent plans and researches; code checks every quote and builds the brief. */
export const SOLO = { id: 'solo' as AgentId, name: 'Solo researcher', role: 'Plan + quick pass', icon: '⚡',
  does: 'Plans the research (the programs that matter most, plus the newest reports), then reads and quotes alone in about 20 seconds. Code checks every quote.' }

export const MODES: { id: 'auto' | 'lean' | 'team'; label: string; hint: string }[] = [
  { id: 'auto', label: 'Auto (recommended)', hint: 'Plan, then a quick one-agent pass; the full team joins only if fewer than 2 facts verify. Usually under 30 seconds.' },
  { id: 'lean', label: 'Quick', hint: 'Plan and quick pass only. Cheapest and fastest; may answer only part of a hard question.' },
  { id: 'team', label: 'Full team', hint: 'Skips the quick pass: the four-agent team with an AI fact-checker. Most thorough; about a minute.' },
]

export const agentName = (id: string | null | undefined) =>
  id === 'solo' ? SOLO.name : AGENTS.find((a) => a.id === id)?.name ?? 'System'

export const SOURCE_NAMES: Record<string, string> = {
  CRS: 'Congressional Research Service',
  GAO: 'Government Accountability Office',
  library: 'Added PDF',
}
