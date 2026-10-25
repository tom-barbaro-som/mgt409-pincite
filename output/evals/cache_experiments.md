# Prompt-caching experiments (October 25, 2026, gpt-6-luna via Portkey)

**Baseline (before):** in the final team suite, every agent's FIRST request read 0 cached tokens (the median, all four agents). Later requests within an agent's own run were 77–79% cached.

**1. Explicit cache breakpoints** (`openai_cache_instructions=True`, `prompt_cache_options.ttl=30m`): the static instructions are sent as a system message carrying `prompt_cache_breakpoint: explicit`, as captured in the request body.
- A bare agent with the 4.4k-token handbook, run three times: A wrote 4,432 tokens; B and C each **read 4,410 cached**.
- Two real lean runs about 6 minutes after earlier runs: the first request **read 5,536 cached** of 5,827.

**2. Reuse across separate runs is intermittent.** The same agent with an identical request missed the cache 3 s, 90 s, and about 100 s after the write (0 cached, a full re-write each time). It hit after about 6 minutes in the case above. Reuse within a run (request 2 onward) is reliable.

**3. Shared "team handbook" across agents** (one identical static prefix, one cache key): agent Y (different tools and output schema) read **0 cached tokens** from agent X's handbook entry, in both the 3 s and the 90 s tests. Because same-agent reuse also missed in those windows, the test can't isolate the cause. But the shared layout adds about 3k tokens to every request, so without demonstrated cross-agent hits it costs more.

**Decision:** per-agent explicit breakpoints are the default (`CACHE_LAYOUT=per_agent`). `CACHE_LAYOUT=shared` remains available for further testing.
