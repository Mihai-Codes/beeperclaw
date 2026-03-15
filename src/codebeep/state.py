"""Lightweight persistent state for the CodeBeep bot."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BotState:
    """Persisted bot state across restarts."""

    active_session_id: str | None = None
    current_model: str | None = None
    seen_event_ids: list[str] = field(default_factory=list)


class StateStore:
    """JSON file-backed state store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> BotState:
        """Load state from disk."""
        if not self.path.exists():
            return BotState()

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("State file is not a JSON object")
            seen_event_ids = data.get("seen_event_ids")
            if not isinstance(seen_event_ids, list):
                seen_event_ids = []
            else:
                seen_event_ids = [e for e in seen_event_ids if isinstance(e, str)]
            return BotState(
                active_session_id=data.get("active_session_id"),
                current_model=data.get("current_model"),
                seen_event_ids=seen_event_ids,
            )
        except Exception as exc:
            logger.warning(f"Failed to load state from {self.path}: {exc}")
            return BotState()

    def save(self, state: BotState) -> None:
        """Persist state to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = asdict(state)
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            tmp_path.replace(self.path)
        except Exception as exc:
            logger.warning(f"Failed to save state to {self.path}: {exc}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
