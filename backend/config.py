"""Paths, model setup (gpt-6-luna via Portkey), and team guardrail limits."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
PROMPTS_DIR = BACKEND_DIR / "prompts"
MCP_CONFIG_PATH = PROJECT_DIR / ".mcp.json"
AUDIT_TRAIL_PATH = Path(os.environ.get("AUDIT_TRAIL_PATH", PROJECT_DIR / "output" / "audit_trail.json"))

# Where PORTKEY_API_KEY is read from (course rule: repo .env first, then one folder up).
# The last entry is a convenience for the MGT 409 class folder, which keeps the key in Portkey.env.
_ENV_CANDIDATES = [PROJECT_DIR / ".env", PROJECT_DIR.parent / ".env", PROJECT_DIR.parent / "Portkey.env"]
ENV_FILE = next((p for p in _ENV_CANDIDATES if p.exists()), None)
if ENV_FILE:
    load_dotenv(ENV_FILE, override=False)  # loads the key into the environment only; never printed or logged

PORTKEY_BASE_URL = os.environ.get("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")
PORTKEY_PROVIDER = os.environ.get("PORTKEY_PROVIDER", "")  # the course key has a default provider attached

# The course key allows gpt-6-luna only. OPENAI_MODEL is the one place the name lives.
ALLOWED_MODELS = {"gpt-6-luna"}
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")
DATASET = os.environ.get("DATASET", "sample")


def _int_env(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ---- Token / loop guardrails (per research case, all agents together) ------
CASE_TOKEN_BUDGET = _int_env("CASE_TOKEN_BUDGET", 250_000)
CASE_REQUEST_LIMIT = _int_env("CASE_REQUEST_LIMIT", 45)        # model calls
CASE_TOOL_CALL_LIMIT = _int_env("CASE_TOOL_CALL_LIMIT", 90)
MAX_DELEGATION_DEPTH = _int_env("MAX_DELEGATION_DEPTH", 3)     # communication -> reader -> search
MAX_DELEGATIONS_PER_CASE = _int_env("MAX_DELEGATIONS_PER_CASE", 8)
MAX_OUTPUT_TOKENS = _int_env("MAX_OUTPUT_TOKENS", 4_000)       # per model response

# Hard per-case MCP limits (enforced by the MCP server; passed to it through .mcp.json env)
MCP_LIMIT_VARS = ("MAX_SEARCHES_PER_CASE", "MAX_SHORTLIST_ROUNDS", "MAX_REPORTS_OPENED", "MAX_PAGES_READ", "MAX_FINDINGS")


class AgentNotConfiguredError(RuntimeError):
    """Raised when the Portkey key isn't available or the model isn't allowed."""


def build_model() -> OpenAIResponsesModel:
    """gpt-6-luna through Portkey's OpenAI-compatible gateway (Responses API)."""
    if MODEL_NAME not in ALLOWED_MODELS:
        raise AgentNotConfiguredError(f"Model '{MODEL_NAME}' is not allowed; the course key covers gpt-6-luna only.")
    api_key = os.environ.get("PORTKEY_API_KEY")
    if not api_key:
        raise AgentNotConfiguredError(
            "PORTKEY_API_KEY is not set. Copy .env.example to .env (in this folder or one level up) and add your key.")
    headers = {"x-portkey-api-key": api_key}
    if PORTKEY_PROVIDER:
        headers["x-portkey-provider"] = PORTKEY_PROVIDER
    client = AsyncOpenAI(api_key=api_key, base_url=PORTKEY_BASE_URL, default_headers=headers)
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


# Prompt caching (gpt-6 supports explicit cache breakpoints; see docs/design.md "Caching").
#   shared    - one identical "team handbook" prefix for every agent + one cache key: a single cached
#               copy serves the whole team (role, tools, and run context come after the breakpoint)
#   per_agent - each agent caches its own instructions under its own key
#   off       - no explicit breakpoint (implicit caching only)
CACHE_LAYOUT = os.environ.get("CACHE_LAYOUT", "per_agent")


def model_settings(agent_name: str) -> OpenAIResponsesModelSettings:
    settings = OpenAIResponsesModelSettings(
        openai_reasoning_effort=REASONING_EFFORT,
        openai_reasoning_summary="concise",          # short reasoning summaries, recorded in the audit trail
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    if CACHE_LAYOUT != "off":
        settings["openai_cache_instructions"] = True            # breakpoint right after the static instructions
        settings["openai_prompt_cache_options"] = {"ttl": "30m"}
        settings["openai_prompt_cache_key"] = ("gov-research-team" if CACHE_LAYOUT == "shared"
                                               else f"gov-research-{agent_name}")
    return settings
