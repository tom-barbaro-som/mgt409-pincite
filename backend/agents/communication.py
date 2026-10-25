"""Communication: the team manager. Talks to the user, plans, delegates, and makes judgment calls."""

from ..models import ManagerReply
from .base import AgentSpec

SPEC = AgentSpec(
    name="communication",
    title="Communication / team manager",
    prompt_file="communication.md",
    output_type=ManagerReply,
    delegate_for="talks to the user, decides scope, delegates, and makes the final call.",
    may_delegate_to=("report_search", "report_reader", "presentation"),
    check_reply_numbers=True,
)
