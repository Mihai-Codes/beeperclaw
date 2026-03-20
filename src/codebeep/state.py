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
    room_sessions: dict[str, str] = field(default_factory=dict)
    last_notified_assistant_message_by_session: dict[str, str] = field(default_factory=dict)
    seen_event_ids: list[str] = field(default_factory=list)
    shell_room_id: str | None = None
    shell_room_alias: str | None = None
    last_bootstrap_attempt: float | None = None


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
            room_sessions = data.get("room_sessions")
            if not isinstance(room_sessions, dict):
                room_sessions = {}
            else:
                room_sessions = {
                    room_id: session_id
                    for room_id, session_id in room_sessions.items()
                    if isinstance(room_id, str) and isinstance(session_id, str)
                }
            last_notified = data.get("last_notified_assistant_message_by_session")
            if not isinstance(last_notified, dict):
                last_notified = {}
            else:
                last_notified = {
                    session_id: message_id
                    for session_id, message_id in last_notified.items()
                    if isinstance(session_id, str) and isinstance(message_id, str)
                }
            return BotState(
                active_session_id=data.get("active_session_id"),
                current_model=data.get("current_model"),
                room_sessions=room_sessions,
                last_notified_assistant_message_by_session=last_notified,
                seen_event_ids=seen_event_ids,
                shell_room_id=data.get("shell_room_id"),
                shell_room_alias=data.get("shell_room_alias"),
                last_bootstrap_attempt=data.get("last_bootstrap_attempt"),
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
