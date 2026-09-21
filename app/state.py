"""Small JSON-backed store of previously delivered article ids (avoids repeats).

The "already sent" memory is kept per language so the Dutch and Japanese halves
of the daily document advance independently.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"seen_guids": {}, "last_delivered": None}
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
        self._migrate()

    def _migrate(self) -> None:
        """Accept the pre-multi-language format, where seen_guids was a flat list."""
        seen = self.data.get("seen_guids")
        if isinstance(seen, list):
            self.data["seen_guids"] = {"nl": [g for g in seen if isinstance(g, str)]}
        elif not isinstance(seen, dict):
            self.data["seen_guids"] = {}
        cleaned: dict[str, list[str]] = {}
        for code, guids in self.data["seen_guids"].items():
            if isinstance(guids, list):
                cleaned[str(code)] = [g for g in guids if isinstance(g, str)]
            else:
                cleaned[str(code)] = []
        self.data["seen_guids"] = cleaned

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # per-language "already delivered" memory                             #
    # ------------------------------------------------------------------ #

    def seen(self, code: str) -> list[str]:
        guids = self.data["seen_guids"].get(code)
        return guids if isinstance(guids, list) else []

    def is_seen(self, code: str, guid: str) -> bool:
        return guid in self.seen(code)

    def mark_seen(self, code: str, guid: str, max_keep: int = 300) -> None:
        guids = self.seen(code)
        if guid not in guids:
            guids.append(guid)
        self.data["seen_guids"][code] = guids[-max_keep:]

    def set_last(self, info: dict[str, Any]) -> None:
        self.data["last_delivered"] = info
