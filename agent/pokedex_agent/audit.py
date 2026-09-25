"""Append-only JSONL audit log of every tool call the agent attempts."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    def __init__(self, path: Path, now: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._now = now

    def write(self, **fields: Any) -> None:
        entry = {"timestamp": self._now().isoformat(), **fields}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
