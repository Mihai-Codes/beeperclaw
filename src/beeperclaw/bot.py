"""Main bot implementation for beeperclaw."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import random
import re
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import simplematrixbotlib as botlib
from nio import (  # type: ignore[import-untyped]
    InviteMemberEvent,
    MegolmEvent,
    RoomEncryptedAudio,
    RoomEncryptedFile,
    RoomEncryptedImage,
    RoomEncryptedVideo,
    RoomMessageAudio,
    RoomMessageFile,
    RoomMessageImage,
    RoomMessageText,
    RoomMessageVideo,
    RoomPreset,
)
from nio.crypto.attachments import decrypt_attachment  # type: ignore[import-untyped]
from nio.responses import (  # type: ignore[import-untyped]
    DiskDownloadResponse,
    DownloadError,
    MemoryDownloadResponse,
    RoomCreateError,
    RoomCreateResponse,
    RoomInviteError,
    RoomInviteResponse,
    RoomPutAliasError,
    RoomResolveAliasError,
    RoomResolveAliasResponse,
)

from beeperclaw.commands import ALL_COMMANDS, Command, CommandContext, CommandResult
from beeperclaw.config import Config
from beeperclaw.opencode_client import OpenCodeClient, PromptAttachment, Session
from beeperclaw.state import BotState, StateStore

logger = logging.getLogger(__name__)

ACTIVE_SESSION_STATUSES = {"running", "waiting"}


@dataclass
class PendingRun:
    """An in-flight room-scoped build/plan request."""

    session_id: str
    room_id: str
    sender: str
    command_name: str
    started_at: float
    state: str
    origin_event_id: str | None = None
    attachments: tuple[PromptAttachment, ...] = ()


class BeeperClawBot:
    """Matrix bot that integrates with OpenCode for mobile coding tasks."""

    def __init__(self, config: Config) -> None:
        """Initialize the bot.

        Args:
            config: Bot configuration
        """
        self.config = config
        auth_config = config.opencode.auth
        auth: tuple[str, str] | None = None
        if auth_config is not None and auth_config.username and auth_config.password:
            auth = (auth_config.username, auth_config.password)

        if auth:
            logger.info("OpenCode auth configured for user %s", auth[0])
        else:
            logger.warning("OpenCode auth not configured; requests may be unauthorized")

        self.opencode = OpenCodeClient(
            base_url=config.opencode.server_url,
            auth=auth,
        )

        # Set up Matrix credentials
        self.creds = botlib.Creds(
            homeserver=config.matrix.homeserver,
            username=config.matrix.username,
            password=config.matrix.password,
            access_token=config.matrix.access_token,
            session_stored_file=None,
            device_name=config.matrix.device_name,
        )

        # Bot configuration
        bot_config = botlib.Config()
        bot_config.encryption_enabled = False
        bot_config.emoji_verify = False
        bot_config.ignore_unverified_devices = True
        state_dir = Path(config.bot.state_path).expanduser().resolve().parent
        bot_config.store_path = str(state_dir / "matrix_store")
        self._attachment_dir = state_dir / "attachments"
        self._attachment_dir.mkdir(parents=True, exist_ok=True)

        self.bot = botlib.Bot(self.creds, bot_config)

        # Register commands
        self.commands: dict[str, Command] = {}
        for cmd_class in ALL_COMMANDS:
            cmd = cmd_class()
            self.commands[cmd.name] = cmd
            # Also register aliases
            for alias in cmd.aliases:
                self.commands[alias] = cmd

        # State
        self.state_store = StateStore(config.bot.state_path)
        self.state: BotState = self.state_store.load()
        self.current_model: str | None = self.state.current_model
        self._room_sessions: dict[str, str] = dict(self.state.room_sessions)
        self._last_notified_assistant_message_by_session: dict[str, str] = dict(
            self.state.last_notified_assistant_message_by_session
        )
        self._staged_attachments_by_room: dict[str, list[PromptAttachment]] = {}
        self._pending_runs: dict[str, PendingRun] = {}
        self._shell_room_id: str | None = self.state.shell_room_id
        self._shell_room_alias: str | None = self.state.shell_room_alias
        self._last_bootstrap_attempt: float | None = self.state.last_bootstrap_attempt
        self._event_task: asyncio.Task[None] | None = None
        self._dedup_enabled = config.bot.dedup_enabled and config.bot.dedup_cache_size > 0
        self._seen_event_ids: deque[str] = deque(
            maxlen=config.bot.dedup_cache_size if self._dedup_enabled else None
        )
        self._seen_event_ids_set: set[str] = set()
        self._message_fingerprint_window = max(0, config.bot.dedup_window_seconds)
        self._message_fingerprints: deque[tuple[float, str]] = deque()
        if self._dedup_enabled and self.state.seen_event_ids:
            seed_ids = [e for e in self.state.seen_event_ids if isinstance(e, str) and e]
            maxlen = self._seen_event_ids.maxlen
            if maxlen is not None and len(seed_ids) > maxlen:
                seed_ids = seed_ids[-maxlen:]
            self._seen_event_ids = deque(seed_ids, maxlen=maxlen)
            self._seen_event_ids_set = set(seed_ids)

        if not self.config.bot.state_path:
            logger.warning("State persistence disabled; session state will reset on restart")
        else:
            logger.info(
                "Loaded state from %s (%s room mappings)",
                self.config.bot.state_path,
                len(self._room_sessions),
            )
            self._save_state()

    def _save_state(self) -> None:
        self.state.active_session_id = None
        self.state.current_model = self.current_model
        self.state.room_sessions = dict(self._room_sessions)
        self.state.last_notified_assistant_message_by_session = dict(
            self._last_notified_assistant_message_by_session
        )
        self.state.shell_room_id = self._shell_room_id
        self.state.shell_room_alias = self._shell_room_alias
        self.state.last_bootstrap_attempt = self._last_bootstrap_attempt
        if self._dedup_enabled:
            self.state.seen_event_ids = list(self._seen_event_ids)
        self.state_store.save(self.state)

    def _get_user_domain(self) -> str:
        username = self.config.matrix.username
        if ":" in username:
            return username.split(":", 1)[1]
        return "matrix.org"

    def set_current_model(self, model: str | None) -> None:
        self.current_model = model
        self._save_state()

    def _remember_event_id(self, event_id: str) -> None:
        if event_id in self._seen_event_ids_set:
            return
        maxlen = self._seen_event_ids.maxlen
        if maxlen is not None and len(self._seen_event_ids) >= maxlen:
            oldest = self._seen_event_ids.popleft()
            self._seen_event_ids_set.discard(oldest)
        self._seen_event_ids.append(event_id)
        self._seen_event_ids_set.add(event_id)
        if self._dedup_enabled:
            self._save_state()

    def _get_event_id(self, event: Any) -> str | None:
        source = getattr(event, "source", None) or {}
        return getattr(event, "event_id", None) or source.get("event_id")

    def _fingerprint_message(self, room_id: str, sender: str, body: str) -> str:
        return f"{room_id}|{sender}|{body.strip()}"

    def _is_duplicate_message(self, fingerprint: str) -> bool:
        if self._message_fingerprint_window <= 0:
            return False
        now = time.time()
        while (
            self._message_fingerprints
            and now - self._message_fingerprints[0][0] > self._message_fingerprint_window
        ):
            self._message_fingerprints.popleft()
        for _, existing in self._message_fingerprints:
            if existing == fingerprint:
                return True
        self._message_fingerprints.append((now, fingerprint))
        return False

    def _get_pending_run_for_room(self, room_id: str) -> PendingRun | None:
        for pending in self._pending_runs.values():
            if pending.room_id == room_id:
                return pending
        return None

    def register_pending_run(
        self,
        *,
        session_id: str,
        room_id: str,
        sender: str,
        command_name: str,
        origin_event_id: str | None,
        state: str,
        attachments: tuple[PromptAttachment, ...] = (),
    ) -> None:
        """Track a running room-scoped command."""
        self._remove_staged_attachments(room_id, attachments)
        self._pending_runs[session_id] = PendingRun(
            session_id=session_id,
            room_id=room_id,
            sender=sender,
            command_name=command_name,
            origin_event_id=origin_event_id,
            started_at=time.time(),
            state=state,
            attachments=attachments,
        )

    def clear_pending_run(self, session_id: str) -> None:
        """Stop tracking an in-flight command."""
        pending = self._pending_runs.pop(session_id, None)
        if pending is not None:
            self._cleanup_attachments(pending.attachments)

    def _cleanup_attachments(self, attachments: Iterable[PromptAttachment]) -> None:
        for attachment in attachments:
            try:
                Path(attachment.path).unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to clean up attachment %s", attachment.path)

    def _expire_staged_attachments(self, room_id: str | None = None) -> None:
        ttl_seconds = max(300, self.config.opencode.session_timeout)
        cutoff = time.time() - ttl_seconds
        room_ids = [room_id] if room_id is not None else list(self._staged_attachments_by_room)

        for current_room_id in room_ids:
            staged = self._staged_attachments_by_room.get(current_room_id)
            if not staged:
                continue
            keep: list[PromptAttachment] = []
            expired: list[PromptAttachment] = []
            for attachment in staged:
                if attachment.created_at and attachment.created_at < cutoff:
                    expired.append(attachment)
                else:
                    keep.append(attachment)

            if expired:
                self._cleanup_attachments(expired)
            if keep:
                self._staged_attachments_by_room[current_room_id] = keep
            else:
                self._staged_attachments_by_room.pop(current_room_id, None)

    def _staged_attachments_for_room(self, room_id: str) -> tuple[PromptAttachment, ...]:
        self._expire_staged_attachments(room_id)
        return tuple(self._staged_attachments_by_room.get(room_id, []))

    def _stage_attachment(self, room_id: str, attachment: PromptAttachment) -> None:
        self._expire_staged_attachments(room_id)
        self._staged_attachments_by_room.setdefault(room_id, []).append(attachment)

    def _remove_staged_attachments(
        self, room_id: str, attachments: Iterable[PromptAttachment]
    ) -> None:
        staged = self._staged_attachments_by_room.get(room_id)
        if not staged:
            return

        attachment_paths = {attachment.path for attachment in attachments}
        remaining = [attachment for attachment in staged if attachment.path not in attachment_paths]
        if remaining:
            self._staged_attachments_by_room[room_id] = remaining
        else:
            self._staged_attachments_by_room.pop(room_id, None)

    async def get_inflight_status_for_room(self, room_id: str) -> tuple[str, str] | None:
        """Check whether a room already has an active task."""
        pending = self._get_pending_run_for_room(room_id)
        if pending is not None:
            return pending.session_id, pending.state

        session_id = self.get_room_session_id(room_id)
        if not session_id:
            return None

        statuses = await self.opencode.get_session_status()
        status = statuses.get(session_id)
        if status is not None and status.status in ACTIVE_SESSION_STATUSES:
            return session_id, status.status
        return None

    def _room_id_for_session(self, session_id: str) -> str | None:
        for room_id, mapped_session_id in self._room_sessions.items():
            if mapped_session_id == session_id:
                return room_id
        return None

    def _assistant_message_already_notified(self, session_id: str, message_id: str) -> bool:
        return self._last_notified_assistant_message_by_session.get(session_id) == message_id

    def _mark_assistant_message_notified(self, session_id: str, message_id: str) -> None:
        self._last_notified_assistant_message_by_session[session_id] = message_id
        self._save_state()

    def get_room_session_id(self, room_id: str) -> str | None:
        """Get the persisted session id for a room, if any."""
        return self._room_sessions.get(room_id)

    def _set_room_session(self, room_id: str, session: Session | None) -> None:
        if session is None:
            self._room_sessions.pop(room_id, None)
        else:
            self._room_sessions[room_id] = session.id
        self._save_state()

    async def get_session_for_room(self, room_id: str) -> Session | None:
        """Resolve the session currently mapped to a room."""
        session_id = self.get_room_session_id(room_id)
        if not session_id:
            return None

        try:
            return await self.opencode.get_session(session_id)
        except Exception:
            logger.warning("Dropping stale room session mapping for %s -> %s", room_id, session_id)
            self._set_room_session(room_id, None)
            return None

    async def get_or_create_session_for_room(self, room_id: str) -> Session:
        """Get or create the room-scoped session."""
        session = await self.get_session_for_room(room_id)
        if session is not None:
            return session

        session = await self.opencode.create_session(title="beeperclaw mobile session")
        self._set_room_session(room_id, session)
        return session

    def is_user_allowed(self, user_id: str) -> bool:
        """Check if a user is allowed to interact with the bot.

        Args:
            user_id: Matrix user ID

        Returns:
            True if allowed
        """
        allowed = self.config.matrix.allowed_users
        if not allowed:
            return True  # No restrictions
        return user_id in allowed

    async def handle_message(self, room: Any, event: Any) -> None:
        """Handle incoming messages.

        Args:
            room: Matrix room
            event: Message event
        """
        # Defensive checks
        if not hasattr(event, "sender") or not event.sender:
            logger.info("DEBUG: Event has no sender, ignoring")
            return

        event_id = self._get_event_id(event)
        if self._dedup_enabled and event_id:
            if event_id in self._seen_event_ids_set:
                logger.info(f"DEBUG: Duplicate event {event_id}, ignoring")
                return
            self._remember_event_id(event_id)

        logger.info(f"DEBUG: handle_message called for room {room.room_id} from {event.sender}")

        # Extract message body from event
        body = (
            event.body
            if hasattr(event, "body")
            else event.source.get("content", {}).get("body", "")
        )
        sender = event.sender

        # Check if message has content
        if not body:
            logger.info("DEBUG: Message has no body, ignoring")
            return

        if self._dedup_enabled and not event_id:
            fingerprint = self._fingerprint_message(room.room_id, sender, body)
            if self._is_duplicate_message(fingerprint):
                logger.info("DEBUG: Duplicate message fingerprint, ignoring")
                return

        # Use MessageMatch to check if message is from bot
        try:
            match = botlib.MessageMatch(room, event, self.bot, self.config.bot.prefix)
            if not match.is_not_from_this_bot():
                logger.info("DEBUG: Message from bot itself, ignoring")
                return
        except Exception as e:
            logger.warning(f"DEBUG: MessageMatch error: {e}, falling back to manual check")
            # Fallback: manually check if sender is the bot
            if sender == self.config.matrix.username:
                logger.info("DEBUG: Message from bot itself (manual check), ignoring")
                return

        logger.info(f"DEBUG: Processing message from {sender}: '{body}'")

        # Check if user is allowed
        if not self.is_user_allowed(sender):
            logger.warning(f"Unauthorized user attempted to use bot: {sender}")
            return

        # Check for command prefix
        if not body.startswith(self.config.bot.prefix):
            return

        # Parse command
        parsed = self._parse_command(body)
        if parsed is None:
            return
        cmd_name, args = parsed
        await self._execute_command(
            room=room,
            sender=sender,
            event_id=event_id,
            cmd_name=cmd_name,
            args=args,
        )

    def _parse_command(self, body: str) -> tuple[str, str] | None:
        parts = body[len(self.config.bot.prefix) :].split(maxsplit=1)
        if not parts:
            return None
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        return cmd_name, args

    async def _execute_command(
        self,
        *,
        room: Any,
        sender: str,
        event_id: str | None,
        cmd_name: str,
        args: str,
    ) -> None:
        cmd = self.commands.get(cmd_name)
        if cmd is None:
            if self.config.bot.unknown_command_reply:
                await self.bot.api.send_text_message(
                    room.room_id,
                    f"Unknown command: {cmd_name}\nUse /help to see available commands.",
                )
            return

        long_running = cmd.name in {"build", "plan"}
        attachments = self._staged_attachments_for_room(room.room_id) if long_running else ()
        use_typing = self.config.bot.typing_indicator and long_running
        context = CommandContext(
            room_id=room.room_id,
            sender=sender,
            event_id=event_id,
            attachments=attachments,
        )

        if use_typing:
            await self.bot.api.async_client.room_typing(room.room_id, True)

        try:
            result = await cmd.execute(self, args, context)
            await self._send_result(
                room.room_id,
                result,
                reply_to_event_id=context.event_id if long_running else None,
            )
        except Exception as e:
            logger.exception(f"Error executing command {cmd_name}")
            await self.bot.api.send_text_message(
                room.room_id,
                f"Error executing command: {e}",
                reply_to=context.event_id if long_running and context.event_id else "",
            )
        finally:
            if use_typing:
                await self.bot.api.async_client.room_typing(room.room_id, False)

    def _attachment_caption(self, event: Any) -> str | None:
        content = getattr(event, "source", {}).get("content", {})
        body = getattr(event, "body", None) or content.get("body")
        filename = content.get("filename")
        if not isinstance(body, str):
            return None
        body = body.strip()
        if not body:
            return None
        if isinstance(filename, str) and filename.strip() and body == filename.strip():
            return None
        return body

    def _attachment_filename(self, event: Any, response_filename: str | None = None) -> str:
        content = getattr(event, "source", {}).get("content", {})
        candidate = content.get("filename") or response_filename or getattr(event, "body", None)
        if not isinstance(candidate, str) or not candidate.strip():
            candidate = "attachment"
        candidate = Path(candidate).name
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
        return sanitized or "attachment"

    def _attachment_mime(self, event: Any) -> str:
        content = getattr(event, "source", {}).get("content", {})
        info = content.get("info", {})
        if isinstance(info, dict):
            mimetype = info.get("mimetype")
            if isinstance(mimetype, str) and mimetype.strip():
                return mimetype
        mimetype = getattr(event, "mimetype", None)
        if isinstance(mimetype, str) and mimetype.strip():
            return mimetype
        return "application/octet-stream"

    def _attachment_destination(self, event_id: str | None, filename: str, mime: str) -> Path:
        safe_name = self._attachment_filename(type("Attachment", (), {"source": {"content": {"filename": filename}}})())
        if "." not in safe_name:
            extension = mimetypes.guess_extension(mime) or ""
            safe_name = f"{safe_name}{extension}"
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", (event_id or f"attachment-{int(time.time())}"))
        return self._attachment_dir / f"{prefix}-{safe_name}"

    async def _download_attachment(self, event: Any) -> PromptAttachment:
        event_id = self._get_event_id(event)
        url = getattr(event, "url", None)
        if not isinstance(url, str) or not url:
            raise ValueError("Attachment is missing a Matrix media URL.")

        mime = self._attachment_mime(event)
        destination = self._attachment_destination(event_id, self._attachment_filename(event), mime)
        caption = self._attachment_caption(event)

        if hasattr(event, "key") and hasattr(event, "hashes") and hasattr(event, "iv"):
            response = await self.bot.api.async_client.download(mxc=url)
            if isinstance(response, DownloadError):
                raise ValueError(f"Attachment download failed: {response.message}")
            ciphertext = response.body if isinstance(response.body, (bytes, bytearray)) else None
            if ciphertext is None:
                raise ValueError("Encrypted attachment download returned no bytes.")
            key = getattr(event, "key", {}).get("k")
            sha256 = getattr(event, "hashes", {}).get("sha256")
            iv = getattr(event, "iv", None)
            if not all(isinstance(value, str) and value for value in (key, sha256, iv)):
                raise ValueError("Encrypted attachment metadata is incomplete.")
            plaintext = decrypt_attachment(bytes(ciphertext), key, sha256, iv)
            destination.write_bytes(plaintext)
            return PromptAttachment(
                path=str(destination.resolve()),
                mime=mime,
                filename=destination.name,
                caption=caption,
            )

        response = await self.bot.api.async_client.download(mxc=url, save_to=destination)
        if isinstance(response, DownloadError):
            raise ValueError(f"Attachment download failed: {response.message}")
        if isinstance(response, MemoryDownloadResponse):
            if not isinstance(response.body, (bytes, bytearray)):
                raise ValueError("Attachment download returned invalid in-memory data.")
            destination.write_bytes(bytes(response.body))
        elif isinstance(response, DiskDownloadResponse):
            downloaded_path = Path(response.body)
            if downloaded_path != destination and downloaded_path.exists():
                downloaded_path.replace(destination)

        filename = self._attachment_filename(event, getattr(response, "filename", None))
        if destination.name != filename and filename:
            normalized = destination.with_name(self._attachment_destination(event_id, filename, mime).name)
            destination.replace(normalized)
            destination = normalized

        return PromptAttachment(
            path=str(destination.resolve()),
            mime=mime,
            filename=filename,
            caption=caption,
            created_at=time.time(),
        )

    async def handle_media_message(self, room: Any, event: Any) -> None:
        """Handle Matrix file/image/audio/video messages."""
        sender = getattr(event, "sender", None)
        if not isinstance(sender, str) or not sender:
            return

        event_id = self._get_event_id(event)
        if self._dedup_enabled and event_id:
            if event_id in self._seen_event_ids_set:
                return
            self._remember_event_id(event_id)

        if sender == self.config.matrix.username:
            return
        if not self.is_user_allowed(sender):
            logger.warning("Unauthorized user attempted to upload attachment: %s", sender)
            return

        try:
            attachment = await self._download_attachment(event)
        except ValueError as exc:
            await self._send_markdown_message(
                room.room_id,
                f"Couldn't use that attachment: {exc}",
                reply_to_event_id=event_id,
            )
            return
        except Exception:
            logger.exception("Failed to process attachment event")
            await self._send_markdown_message(
                room.room_id,
                "Couldn't use that attachment due to an unexpected download error.",
                reply_to_event_id=event_id,
            )
            return

        self._stage_attachment(room.room_id, attachment)
        caption = attachment.caption or ""
        if caption.startswith(self.config.bot.prefix):
            parsed = self._parse_command(caption)
            if parsed is not None:
                cmd_name, args = parsed
                cmd = self.commands.get(cmd_name)
                if cmd is not None and cmd.name in {"build", "plan"}:
                    await self._execute_command(
                        room=room,
                        sender=sender,
                        event_id=event_id,
                        cmd_name=cmd_name,
                        args=args,
                    )
                    return

            await self._send_markdown_message(
                room.room_id,
                "Attachment saved. Use `/build` or `/plan` to consume staged files in this room.",
                reply_to_event_id=event_id,
            )
            return

        await self._send_markdown_message(
            room.room_id,
            f"Saved attachment `{attachment.filename}` for the next `/build` or `/plan` in this room.",
            reply_to_event_id=event_id,
        )

    def _extract_retry_after(self, payload: Any) -> float | None:
        if isinstance(payload, dict):
            retry_after_ms = payload.get("retry_after_ms")
            if isinstance(retry_after_ms, (int, float)) and retry_after_ms > 0:
                return retry_after_ms / 1000.0
        return None

    def _parse_transport_payload(self, transport_response: Any) -> dict[str, Any] | None:
        content = getattr(transport_response, "content", None)
        if not content:
            return None
        try:
            if isinstance(content, (bytes, bytearray)):
                content = content.decode("utf-8", errors="ignore")
            if isinstance(content, str):
                payload = json.loads(content)
                return payload if isinstance(payload, dict) else None
        except Exception:
            return None
        return None

    def _rate_limited(self, response: Any) -> float | None:
        errcode = getattr(response, "errcode", None)
        if errcode == "M_LIMIT_EXCEEDED":
            retry_after_ms = getattr(response, "retry_after_ms", None)
            if isinstance(retry_after_ms, (int, float)) and retry_after_ms > 0:
                return retry_after_ms / 1000.0
            return -1.0

        transport = getattr(response, "transport_response", None)
        if transport:
            status = getattr(transport, "status", None) or getattr(transport, "status_code", None)
            if status == 429:
                payload = self._parse_transport_payload(transport)
                retry_after = self._extract_retry_after(payload)
                return retry_after if retry_after is not None else -1.0

        message = str(getattr(response, "message", "")).lower()
        if "m_limit_exceeded" in message or "too many requests" in message:
            return -1.0

        return None

    async def _retry_matrix_call(
        self,
        label: str,
        func: Any,
        *args: Any,
        max_retries: int = 5,
        base_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        delay = base_delay
        last_response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await func(*args, **kwargs)
            except Exception as exc:
                message = str(exc).lower()
                if (
                    "m_limit_exceeded" in message
                    or "too many requests" in message
                    or "429" in message
                ):
                    retry_after = None
                    sleep_for = delay
                    jitter = random.uniform(0, 0.5)
                    logger.warning(
                        f"{label} rate limited (attempt {attempt}/{max_retries}), retrying in {sleep_for:.2f}s"
                    )
                    await asyncio.sleep(sleep_for + jitter)
                    delay = min(delay * 2, 30.0)
                    continue
                raise
            last_response = response
            retry_after = self._rate_limited(response)
            if retry_after is None:
                return response
            sleep_for = retry_after if retry_after > 0 else delay
            jitter = random.uniform(0, 0.5)
            logger.warning(
                f"{label} rate limited (attempt {attempt}/{max_retries}), retrying in {sleep_for:.2f}s"
            )
            await asyncio.sleep(sleep_for + jitter)
            delay = min(delay * 2, 30.0)

        logger.error(f"{label} failed after {max_retries} attempts due to rate limiting")
        return last_response

    async def _resolve_room_alias(self, alias: str) -> str | None:
        response = await self._retry_matrix_call(
            "Room alias resolve", self.bot.api.async_client.room_resolve_alias, alias
        )
        if isinstance(response, RoomResolveAliasResponse):
            return response.room_id if isinstance(response.room_id, str) else None
        if isinstance(response, RoomResolveAliasError):
            return None
        room_id = getattr(response, "room_id", None)
        return room_id if isinstance(room_id, str) else None

    async def _bootstrap_shell_room(self) -> None:
        domain = self._get_user_domain()
        alias = f"#beeperclaw-shell:{domain}"
        now = time.time()
        if self._last_bootstrap_attempt and now - self._last_bootstrap_attempt < 300:
            logger.info("Skipping shell room bootstrap due to recent attempt")
            return

        self._last_bootstrap_attempt = now
        self._save_state()

        if self._shell_room_id:
            logger.info(f"Shell room already recorded: {self._shell_room_id}")
            return

        if self._shell_room_alias and self._shell_room_alias != alias:
            logger.info(
                f"Shell room alias changed from {self._shell_room_alias} to {alias}, rechecking"
            )
            self._shell_room_alias = alias
            self._save_state()
        existing_room_id = await self._resolve_room_alias(alias)
        if existing_room_id:
            logger.info(f"Shell room already exists: {existing_room_id}")
            self._shell_room_id = existing_room_id
            self._shell_room_alias = alias
            self._save_state()
            return

        logger.info("Bootstrapping: Creating BeeperClaw Shell room...")
        response = await self._retry_matrix_call(
            "Room create",
            self.bot.api.async_client.room_create,
            name="BeeperClaw Shell",
            topic="Unencrypted command shell for BeeperClaw",
            preset=RoomPreset.private_chat,
        )

        if isinstance(response, RoomCreateError):
            logger.error(f"Room create failed: {response}")
            return

        if isinstance(response, RoomCreateResponse):
            room_id = response.room_id
        else:
            room_id = getattr(response, "room_id", None)

        if not room_id:
            logger.error(f"Room create returned no room_id: {response}")
            return

        self._shell_room_id = room_id
        self._shell_room_alias = alias
        self._save_state()

        logger.info(f"Created room: {room_id}")

        alias_resp = await self._retry_matrix_call(
            "Room alias",
            self.bot.api.async_client.room_put_alias,
            room_alias=alias,
            room_id=room_id,
        )

        if isinstance(alias_resp, RoomPutAliasError):
            logger.error(f"Failed to set room alias: {alias_resp}")
        else:
            logger.info(f"Alias response: {alias_resp}")

        logger.info("==================================================")
        logger.info(f"JOIN LINK: https://matrix.to/#/{alias}")
        logger.info(f"ROOM ID: {room_id}")
        logger.info("==================================================")

        invitees: Iterable[str] = self.config.matrix.allowed_users or []
        for user_id in invitees:
            invite_resp = await self._retry_matrix_call(
                f"Invite {user_id}",
                self.bot.api.async_client.room_invite,
                room_id=room_id,
                user_id=user_id,
            )
            if isinstance(invite_resp, RoomInviteError):
                logger.error(f"Invite failed for {user_id}: {invite_resp}")
            elif isinstance(invite_resp, RoomInviteResponse):
                logger.info(f"Invited {user_id} to room {room_id}")
            else:
                logger.info(f"Invite response for {user_id}: {invite_resp}")

    async def _send_markdown_message(
        self, room_id: str, message: str, *, reply_to_event_id: str | None = None
    ) -> None:
        """Send a markdown Matrix message with optional reply metadata."""
        content = {
            "msgtype": "m.text",
            "body": message,
            "format": "org.matrix.custom.html",
            "formatted_body": markdown.markdown(message, extensions=["fenced_code", "nl2br"]),
        }
        if reply_to_event_id:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}

        await self._retry_matrix_call(
            "Send markdown message",
            self.bot.api.async_client.room_send,
            room_id=room_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )

    async def _send_result(
        self, room_id: str, result: CommandResult, *, reply_to_event_id: str | None = None
    ) -> None:
        """Send a command result to a room."""
        message = result.message
        max_len = self.config.bot.max_message_length
        parts = [message[i : i + max_len] for i in range(0, len(message), max_len)]
        for index, part in enumerate(parts):
            await self._send_markdown_message(
                room_id,
                part,
                reply_to_event_id=reply_to_event_id if index == 0 else None,
            )

    async def _recover_pending_runs(self) -> None:
        """Rebuild pending runs from persisted room mappings after a restart."""
        if not self._room_sessions:
            return

        statuses = await self.opencode.get_session_status()
        now = time.time()
        for room_id, session_id in self._room_sessions.items():
            status = statuses.get(session_id)
            if status is None or status.status not in ACTIVE_SESSION_STATUSES:
                continue
            if session_id in self._pending_runs:
                continue
            self._pending_runs[session_id] = PendingRun(
                session_id=session_id,
                room_id=room_id,
                sender="",
                command_name="task",
                origin_event_id=None,
                started_at=now,
                state=status.status,
            )
            logger.info(
                "Recovered pending room task for %s in session %s", room_id, session_id[:8]
            )

    def _format_completion_message(
        self, pending: PendingRun, session_id: str, body: str | None, state: str
    ) -> str:
        lines = [
            f"{pending.command_name.capitalize()} finished.",
            f"Session: `{session_id[:8]}...`",
            f"State: `{state}`",
        ]
        if body:
            lines.append("")
            lines.append(body)
        return "\n".join(lines)

    async def _notify_completion(
        self,
        session_id: str,
        pending: PendingRun,
        *,
        body: str | None,
        state: str,
        assistant_message_id: str | None = None,
    ) -> None:
        await self._send_markdown_message(
            pending.room_id,
            self._format_completion_message(pending, session_id, body, state),
            reply_to_event_id=pending.origin_event_id,
        )
        if assistant_message_id:
            self._mark_assistant_message_notified(session_id, assistant_message_id)
        self.clear_pending_run(session_id)

    async def _maybe_notify_terminal_session(self, session_id: str) -> None:
        pending = self._pending_runs.get(session_id)
        if pending is None:
            return

        statuses = await self.opencode.get_session_status()
        status = statuses.get(session_id)
        if status is not None and status.status in ACTIVE_SESSION_STATUSES:
            pending.state = status.status
            return

        try:
            messages = await self.opencode.get_messages(session_id, limit=20)
        except Exception:
            logger.exception("Failed to load messages for session %s", session_id)
            messages = []

        for message in reversed(messages):
            if message.role != "assistant":
                continue
            if self._assistant_message_already_notified(session_id, message.id):
                self.clear_pending_run(session_id)
                return
            await self._notify_completion(
                session_id,
                pending,
                body=self.opencode.get_message_text(message),
                state="completed",
                assistant_message_id=message.id,
            )
            return

        await self._notify_completion(
            session_id,
            pending,
            body="Task finished. Inspect the session for the final output.",
            state=status.status if status is not None else "completed",
        )

    async def _monitor_events(self) -> None:
        """Monitor OpenCode events and notify users of completions."""
        try:
            async for event in self.opencode.subscribe_events():
                session_id = self.opencode.extract_session_id_from_event(event)
                if session_id is None or session_id not in self._pending_runs:
                    continue

                assistant_message = self.opencode.extract_assistant_message_from_event(event)
                if assistant_message is not None:
                    if self._assistant_message_already_notified(session_id, assistant_message.id):
                        self.clear_pending_run(session_id)
                        continue
                    pending = self._pending_runs.get(session_id)
                    if pending is None:
                        continue
                    await self._notify_completion(
                        session_id,
                        pending,
                        body=self.opencode.get_message_text(assistant_message),
                        state="completed",
                        assistant_message_id=assistant_message.id,
                    )
                    continue

                await self._maybe_notify_terminal_session(session_id)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"Error monitoring events: {e}")

    async def start(self) -> None:
        """Start the bot."""
        logger.info("Starting beeperclaw bot...")

        # Verify OpenCode connection
        try:
            health = await self.opencode.health_check()
            logger.info(f"Connected to OpenCode server: {health.get('version', 'unknown')}")
        except Exception as e:
            logger.error(f"Failed to connect to OpenCode server: {e}")
            raise

        # Run the bot
        logger.info("Bot is running. Waiting for messages...")
        await self.bot.api.login()

        # Register message handler
        async def on_message(room: Any, event: Any) -> None:
            await self.handle_message(room, event)

        self.bot.api.async_client.add_event_callback(on_message, RoomMessageText)

        async def on_media(room: Any, event: Any) -> None:
            await self.handle_media_message(room, event)

        media_event_types = (
            RoomMessageImage,
            RoomMessageFile,
            RoomMessageVideo,
            RoomMessageAudio,
            RoomEncryptedImage,
            RoomEncryptedFile,
            RoomEncryptedVideo,
            RoomEncryptedAudio,
        )
        for event_type in media_event_types:
            self.bot.api.async_client.add_event_callback(on_media, event_type)

        # Register invite handler
        async def on_invite(room: Any, event: Any) -> None:
            if not isinstance(event, InviteMemberEvent):
                return
            sender = event.sender
            if self.is_user_allowed(sender):
                logger.info(f"Joining room {room.room_id} invited by {sender}")
                await self.bot.api.async_client.join(room.room_id)
            else:
                logger.warning(f"Ignoring invite from {sender} to {room.room_id}")

        self.bot.api.async_client.add_event_callback(on_invite, InviteMemberEvent)

        # Debug: Log encrypted events
        async def on_encrypted(room: Any, event: MegolmEvent) -> None:
            logger.info(f"DEBUG: Received Encrypted Event in {room.room_id} from {event.sender}")

        self.bot.api.async_client.add_event_callback(on_encrypted, MegolmEvent)

        # Debug: Log decrypted text events
        async def on_text_debug(room: Any, event: RoomMessageText) -> None:
            logger.info(
                f"DEBUG: Received Decrypted Text in {room.room_id} from {event.sender}: '{event.body}'"
            )

        self.bot.api.async_client.add_event_callback(on_text_debug, RoomMessageText)

        try:
            await self._recover_pending_runs()
        except Exception:
            logger.exception("Failed to recover pending room tasks")

        # Start event monitoring
        self._event_task = asyncio.create_task(self._monitor_events())

        # Bootstrap: Create unencrypted room
        try:
            await self._bootstrap_shell_room()
        except Exception as e:
            logger.error(f"Bootstrap error: {e}")

        await self.bot.api.async_client.sync_forever(timeout=30000)

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping beeperclaw bot...")

        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass

        await self.opencode.close()
        logger.info("Bot stopped.")


async def run_bot(config: Config) -> None:
    """Run the bot with the given configuration.

    Args:
        config: Bot configuration
    """
    bot = BeeperClawBot(config)
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()
