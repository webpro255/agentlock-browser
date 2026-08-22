"""JSONL decision log.

Every gate decision and every provenance record is appended here as one JSON
object per line.  This file is the ground truth for the pre-registered tests
in PREDICTIONS.md -- never a chat summary, never a tool's own return value.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

__all__ = ["DecisionLog"]


class DecisionLog:
    """Append-only JSONL writer.

    Opened in append mode on every write and flushed immediately: a crash
    mid-session must not cost the decisions already made.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
        }
        record.update(fields)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            fh.flush()
        return record

    def read(self) -> list[dict[str, Any]]:
        """Read the log back.  Used by tests; never by the server itself."""
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def decisions(self) -> list[dict[str, Any]]:
        return [r for r in self.read() if r.get("event") == "decision"]

    def denials(self) -> list[dict[str, Any]]:
        return [r for r in self.decisions() if not r.get("allowed")]
