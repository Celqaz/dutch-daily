"""Small JSON-backed store of previously delivered article ids (avoids repeats)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"seen_guids": [], "last_delivered": None}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass
        self.data.setdefault("seen_guids", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def is_seen(self, guid: str) -> bool:
        return guid in self.data["seen_guids"]

    def mark_seen(self, guid: str, max_keep: int = 300) -> None:
        if guid not in self.data["seen_guids"]:
            self.data["seen_guids"].append(guid)
        self.data["seen_guids"] = self.data["seen_guids"][-max_keep:]

    def set_last(self, info: dict[str, Any]) -> None:
        self.data["last_delivered"] = info
