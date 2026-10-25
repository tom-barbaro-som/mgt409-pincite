"""Presentation: fact-checks every finding, screenshots the evidence, and compiles the cited brief."""

from ..models import PresentationReport
from .base import AgentSpec

SPEC = AgentSpec(
    name="presentation",
    title="Presentation / fact-checker",
    prompt_file="presentation.md",
    output_type=PresentationReport,
    delegate_for="independently checks findings against the PDF pages, rejects weak ones, builds the brief.",
    may_delegate_to=("report_reader",),
)
