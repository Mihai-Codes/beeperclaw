"""Tests for room-scoped bot behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nio.responses import DiskDownloadResponse, DownloadError

import beeperclaw.bot as bot_module
from beeperclaw.bot import BeeperClawBot
from beeperclaw.commands import AbortCommand, BuildCommand, CommandContext, PlanCommand, StatusCommand
from beeperclaw.config import Config
from beeperclaw.opencode_client import Session, SessionStatus


@dataclass
class _SentMessage:
    room_id: str
    content: dict
    message_type: str


class _DummyAsyncClient:
    def __init__(self) -> None:
        self.sent_messages: list[_SentMessage] = []
        self.download = AsyncMock(side_effect=self._download_impl)

    async def room_send(self, *, room_id: str, message_type: str, content: dict, **kwargs):
        self.sent_messages.append(
            _SentMessage(room_id=room_id, content=content, message_type=message_type)
        )
        return object()

    async def room_typing(self, room_id: str, typing_state: bool):
        return object()

    async def _download_impl(self, *, mxc: str, save_to=None, **kwargs):
        del mxc, kwargs
        if save_to is None:
            raise AssertionError("download test double expected save_to")
        path = Path(save_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"attachment")
        return DiskDownloadResponse(body=path, content_type="image/png", filename=path.name)

    def add_event_callback(self, callback, event_type) -> None:
        return None


class _DummyApi:
    def __init__(self) -> None:
        self.async_client = _DummyAsyncClient()

    async def send_text_message(self, room_id: str, message: str, reply_to: str = ""):
        return object()

    async def send_markdown_message(self, room_id: str, message: str):
        return object()

    async def login(self):
        return object()


class _DummyMatrixBot:
    def __init__(self, creds, config) -> None:
        self.api = _DummyApi()
        self.config = config


def _make_config(tmp_path) -> Config:
    return Config.model_validate(
        {
            "matrix": {
                "homeserver": "https://matrix.example.org",
                "username": "@beeperclaw:test",
                "access_token": "token",
                "allowed_users": ["@mihai:matrix.org"],
            },
            "opencode": {"server_url": "http://127.0.0.1:4096"},
            "bot": {"state_path": str(tmp_path / "state.json")},
        }
    )


@pytest.fixture
def bot_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_module.botlib, "Bot", _DummyMatrixBot)

    def factory(state_path=None) -> BeeperClawBot:
        cfg = _make_config(tmp_path)
        if state_path is not None:
            cfg.bot.state_path = str(state_path)
        return BeeperClawBot(cfg)

    return factory


def _session(session_id: str) -> Session:
    return Session(
        id=session_id,
        title="Session",
        parent_id=None,
        created_at="",
        updated_at="",
    )


def _assistant_event(bot: BeeperClawBot, *, session_id: str = "sess-1", message_id: str = "msg-1"):
    return bot.opencode.normalize_event(
        {
            "type": "session.message",
            "sessionID": session_id,
            "message": {
                "id": message_id,
                "info": {
                    "id": message_id,
                    "sessionID": session_id,
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Completed successfully"}],
                },
            },
        }
    )


def _room(room_id: str = "!room:example.org") -> SimpleNamespace:
    return SimpleNamespace(room_id=room_id)


def _text_event(body: str, *, event_id: str = "$text") -> SimpleNamespace:
    return SimpleNamespace(
        sender="@mihai:matrix.org",
        body=body,
        event_id=event_id,
        source={"event_id": event_id, "content": {"body": body}},
    )


def _attachment_event(
    *,
    body: str,
    filename: str,
    mimetype: str = "image/png",
    event_id: str = "$attachment",
) -> SimpleNamespace:
    return SimpleNamespace(
        sender="@mihai:matrix.org",
        body=body,
        url="mxc://matrix.example.org/media",
        event_id=event_id,
        source={
            "event_id": event_id,
            "content": {
                "body": body,
                "filename": filename,
                "url": "mxc://matrix.example.org/media",
                "info": {"mimetype": mimetype},
            },
        },
    )


@pytest.mark.asyncio
async def test_room_sessions_are_persisted_per_room(bot_factory, tmp_path) -> None:
    state_path = tmp_path / "state.json"
    bot = bot_factory(state_path)
    bot.opencode.create_session = AsyncMock(side_effect=[_session("sess-a"), _session("sess-b")])

    session_a = await bot.get_or_create_session_for_room("!room-a:example.org")
    session_b = await bot.get_or_create_session_for_room("!room-b:example.org")

    assert session_a.id == "sess-a"
    assert session_b.id == "sess-b"
    assert bot.get_room_session_id("!room-a:example.org") == "sess-a"
    assert bot.get_room_session_id("!room-b:example.org") == "sess-b"

    restored = bot_factory(state_path)

    async def fake_get_session(session_id: str) -> Session:
        return _session(session_id)

    restored.opencode.get_session = AsyncMock(side_effect=fake_get_session)

    room_a_session = await restored.get_session_for_room("!room-a:example.org")
    room_b_session = await restored.get_session_for_room("!room-b:example.org")

    assert room_a_session is not None and room_a_session.id == "sess-a"
    assert room_b_session is not None and room_b_session.id == "sess-b"


@pytest.mark.asyncio
@pytest.mark.parametrize("command_cls", [BuildCommand, PlanCommand])
async def test_long_running_commands_reject_busy_room(bot_factory, command_cls) -> None:
    bot = bot_factory()
    bot.get_inflight_status_for_room = AsyncMock(return_value=("sess-1", "waiting"))  # type: ignore[method-assign]

    context = CommandContext(
        room_id="!room:example.org",
        sender="@mihai:matrix.org",
        event_id="$event",
    )
    result = await command_cls().execute(bot, "do the thing", context)

    assert result.success is False
    assert "Wait for it to finish" in result.message


@pytest.mark.asyncio
async def test_status_defaults_to_current_room_session(bot_factory) -> None:
    bot = bot_factory()
    bot._room_sessions["!room:example.org"] = "sess-1"
    bot.opencode.get_session_status = AsyncMock(
        return_value={"sess-1": SessionStatus(session_id="sess-1", status="running", agent="build")}
    )

    result = await StatusCommand().execute(
        bot,
        "",
        CommandContext(room_id="!room:example.org", sender="@mihai:matrix.org"),
    )

    assert result.success is True
    assert "`sess-1...`" in result.message
    assert "[running]" in result.message


@pytest.mark.asyncio
async def test_abort_defaults_to_current_room_session(bot_factory) -> None:
    bot = bot_factory()
    bot._room_sessions["!room:example.org"] = "sess-1"
    bot.opencode.get_session_status = AsyncMock(
        return_value={"sess-1": SessionStatus(session_id="sess-1", status="running")}
    )
    bot.opencode.abort_session = AsyncMock(return_value=True)
    bot.register_pending_run(
        session_id="sess-1",
        room_id="!room:example.org",
        sender="@mihai:matrix.org",
        command_name="build",
        origin_event_id="$origin",
        state="running",
    )

    result = await AbortCommand().execute(
        bot,
        "",
        CommandContext(room_id="!room:example.org", sender="@mihai:matrix.org"),
    )

    assert result.success is True
    bot.opencode.abort_session.assert_awaited_once_with("sess-1")
    assert "sess-1" not in bot._pending_runs


@pytest.mark.asyncio
async def test_assistant_completion_notifies_once_with_reply(bot_factory) -> None:
    bot = bot_factory()
    bot.register_pending_run(
        session_id="sess-1",
        room_id="!room:example.org",
        sender="@mihai:matrix.org",
        command_name="build",
        origin_event_id="$origin",
        state="running",
    )

    async def fake_events():
        event = _assistant_event(bot)
        yield event
        yield event

    bot.opencode.subscribe_events = fake_events  # type: ignore[method-assign]

    await bot._monitor_events()

    sent_messages = bot.bot.api.async_client.sent_messages
    assert len(sent_messages) == 1
    assert sent_messages[0].room_id == "!room:example.org"
    assert sent_messages[0].content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$origin"
    assert bot._last_notified_assistant_message_by_session["sess-1"] == "msg-1"
    assert "sess-1" not in bot._pending_runs


@pytest.mark.asyncio
async def test_restart_recovers_pending_run_and_persists_notification_dedup(
    bot_factory, tmp_path
) -> None:
    state_path = tmp_path / "state.json"

    first_bot = bot_factory(state_path)
    first_bot._room_sessions["!room:example.org"] = "sess-1"
    first_bot._save_state()

    recovered_bot = bot_factory(state_path)
    recovered_bot.opencode.get_session_status = AsyncMock(
        return_value={"sess-1": SessionStatus(session_id="sess-1", status="running")}
    )
    await recovered_bot._recover_pending_runs()

    async def fake_events():
        event = _assistant_event(recovered_bot)
        yield event
        yield event

    recovered_bot.opencode.subscribe_events = fake_events  # type: ignore[method-assign]

    await recovered_bot._monitor_events()

    sent_messages = recovered_bot.bot.api.async_client.sent_messages
    assert len(sent_messages) == 1
    assert sent_messages[0].room_id == "!room:example.org"
    assert "m.relates_to" not in sent_messages[0].content

    dedup_bot = bot_factory(state_path)
    dedup_bot.register_pending_run(
        session_id="sess-1",
        room_id="!room:example.org",
        sender="",
        command_name="task",
        origin_event_id=None,
        state="running",
    )

    async def duplicate_event():
        yield _assistant_event(dedup_bot)

    dedup_bot.opencode.subscribe_events = duplicate_event  # type: ignore[method-assign]

    await dedup_bot._monitor_events()

    assert dedup_bot.bot.api.async_client.sent_messages == []


@pytest.mark.asyncio
async def test_attachment_is_staged_for_next_long_running_command(bot_factory) -> None:
    bot = bot_factory()
    bot.get_inflight_status_for_room = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot.get_or_create_session_for_room = AsyncMock(return_value=_session("sess-1"))  # type: ignore[method-assign]
    bot.opencode.send_message_async = AsyncMock()

    await bot.handle_media_message(_room(), _attachment_event(body="error.png", filename="error.png"))

    assert len(bot._staged_attachments_for_room("!room:example.org")) == 1
    assert "Saved attachment" in bot.bot.api.async_client.sent_messages[-1].content["body"]

    await bot.handle_message(_room(), _text_event("/build investigate this"))

    bot.opencode.send_message_async.assert_awaited_once()
    send_kwargs = bot.opencode.send_message_async.await_args.kwargs
    assert len(send_kwargs["attachments"]) == 1
    assert bot._staged_attachments_for_room("!room:example.org") == ()

    pending = bot._pending_runs["sess-1"]
    attachment_path = Path(pending.attachments[0].path)
    assert attachment_path.exists()

    bot.clear_pending_run("sess-1")
    assert not attachment_path.exists()


@pytest.mark.asyncio
async def test_attachment_caption_can_start_plan_immediately(bot_factory) -> None:
    bot = bot_factory()
    bot.get_inflight_status_for_room = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot.get_or_create_session_for_room = AsyncMock(return_value=_session("sess-2"))  # type: ignore[method-assign]
    bot.opencode.send_message_async = AsyncMock()

    await bot.handle_media_message(
        _room(),
        _attachment_event(
            body="/plan inspect this screenshot",
            filename="screenshot.png",
            event_id="$img2",
        ),
    )

    bot.opencode.send_message_async.assert_awaited_once()
    sent_messages = bot.bot.api.async_client.sent_messages
    assert any("Analysis started with plan agent." in message.content["body"] for message in sent_messages)
    assert bot._staged_attachments_for_room("!room:example.org") == ()


@pytest.mark.asyncio
async def test_attachment_download_failure_returns_helpful_reply(bot_factory) -> None:
    bot = bot_factory()
    bot.bot.api.async_client.download = AsyncMock(return_value=DownloadError("boom"))  # type: ignore[method-assign]

    await bot.handle_media_message(_room(), _attachment_event(body="error.log", filename="error.log"))

    sent_messages = bot.bot.api.async_client.sent_messages
    assert len(sent_messages) == 1
    assert "Couldn't use that attachment" in sent_messages[0].content["body"]
    assert bot._staged_attachments_for_room("!room:example.org") == ()
