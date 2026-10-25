"""Solo researcher: lean mode. One agent searches, reads, and quotes; code verifies and builds the brief."""

from ..models import LeanAnswer
from .base import AgentSpec

SPEC = AgentSpec(
    name="solo",
    title="Solo researcher (lean mode)",
    prompt_file="solo.md",
    output_type=LeanAnswer,
    delegate_for="answers simple questions alone; code verifies its quotes and builds the brief.",
    may_delegate_to=(),
    check_reply_numbers=True,
)
