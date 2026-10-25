"""Append-only audit trail for every agent-loop step.

output/audit_trail.json is written as JSON Lines: one complete JSON object per
line. The file is only ever opened in append mode ("a"), so existing entries
are never rewritten or erased. Read it with read_audit_trail().
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AuditEntry

MAX_FIELD_CHARS = 4_000  # keep single entries readable; long tool results are clipped


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"... [clipped {len(value) - MAX_FIELD_CHARS} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


class AuditTrail:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: str, **fields: Any) -> AuditEntry:
        detail = fields.pop("detail", {}) or {}
        entry = AuditEntry(
            ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            event=event,
            detail=_clip(json.loads(json.dumps(detail, default=str))),
            **{k: _clip(v) for k, v in fields.items()},
        )
        line = entry.model_dump_json(exclude_none=True)
        with self._lock, self.path.open("a", encoding="utf-8") as f:  # append only - never "w"
            f.write(line + "\n")
        return entry


def read_audit_trail(path: Path, run_id: str | None = None) -> list[dict[str, Any]]:
    """Parse the JSON Lines audit file (optionally one run) without modifying it."""
    if not Path(path).exists():
        return []
    entries = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                if run_id is None or item.get("run_id") == run_id:
                    entries.append(item)
    return entries
