"""Report Search: turns a request into ranked concept queries and files the best 1-3 reports."""

from ..models import SearchReport
from .base import AgentSpec

SPEC = AgentSpec(
    name="report_search",
    title="Report Search",
    prompt_file="report_search.md",
    output_type=SearchReport,
    delegate_for="ranks the CRS/GAO catalog and files the best 1-3 reports on the case board (metadata only).",
    may_delegate_to=(),
)
