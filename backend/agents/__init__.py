"""The research agents (four-agent team + lean-mode solo researcher). Each file defines one spec."""

from .base import AgentSpec, build_agent
from .communication import SPEC as COMMUNICATION
from .presentation import SPEC as PRESENTATION
from .report_reader import SPEC as REPORT_READER
from .report_search import SPEC as REPORT_SEARCH
from .solo import SPEC as SOLO

ALL_SPECS: list[AgentSpec] = [COMMUNICATION, REPORT_SEARCH, REPORT_READER, PRESENTATION, SOLO]

__all__ = ["ALL_SPECS", "AgentSpec", "build_agent"]
