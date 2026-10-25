"""Report Reader: opens shortlisted reports, ranks their pages, reads passages, records quote-backed findings."""

from ..models import ReaderReport
from .base import AgentSpec

SPEC = AgentSpec(
    name="report_reader",
    title="Report Reader",
    prompt_file="report_reader.md",
    output_type=ReaderReport,
    delegate_for="reads only the top-ranked pages of shortlisted reports and records verbatim, page-cited findings.",
    may_delegate_to=("report_search",),
)
