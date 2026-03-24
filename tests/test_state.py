"""Tests for persisted bot state."""

from __future__ import annotations

from beeperclaw.state import BotState, StateStore


def test_state_store_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    original = BotState(
        current_model="claude-opus-4.5",
        room_sessions={"!room-a:example.org": "sess-a", "!room-b:example.org": "sess-b"},
        last_notified_assistant_message_by_session={"sess-a": "msg-a"},
        seen_event_ids=["evt-1"],
    )

    store.save(original)
    loaded = store.load()

    assert loaded.current_model == original.current_model
    assert loaded.room_sessions == original.room_sessions
    assert (
        loaded.last_notified_assistant_message_by_session
        == original.last_notified_assistant_message_by_session
    )
    assert loaded.seen_event_ids == original.seen_event_ids
