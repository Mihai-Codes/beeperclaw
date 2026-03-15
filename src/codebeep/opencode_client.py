"""OpenCode Server API client."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class OpenCodeError(Exception):
    """Base OpenCode client error."""


class OpenCodeAPIError(OpenCodeError):
    """HTTP/API error from OpenCode."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.retry_after = retry_after


class OpenCodeRateLimitError(OpenCodeAPIError):
    """Rate limiting error from OpenCode."""


class OpenCodeInvalidResponseError(OpenCodeError):
    """Raised when OpenCode returns an unexpected payload."""


@dataclass
class Session:
    """Represents an OpenCode session."""

    id: str
    title: str | None
    parent_id: str | None
    created_at: str
    updated_at: str
    share: dict[str, Any] | None = None
    slug: str | None = None
    version: str | None = None
    project_id: str | None = None
    directory: str | None = None


@dataclass
class Message:
    """Represents a message in a session."""

    id: str
    session_id: str
    role: str
    created_at: str
    parts: list[dict[str, Any]]
    agent: str | None = None
    model: dict[str, str] | None = None
    parent_id: str | None = None


@dataclass
class SessionStatus:
    """Status of a session."""

    session_id: str
    status: str  # "idle", "running", "waiting"
    agent: str | None = None
    model: str | None = None


class OpenCodeClient:
    """Client for interacting with the OpenCode server API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        timeout: float = 30.0,
        max_retries: int = 3,
        auth: tuple[str, str] | None = None,
    ) -> None:
        """Initialize the OpenCode client.

        Args:
            base_url: Base URL of the OpenCode server
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.auth = auth
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                auth=self.auth,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _safe_json(self, response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    def _parse_retry_after(self, response: httpx.Response, payload: Any | None) -> float | None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        if isinstance(payload, dict):
            retry_after_ms = payload.get("retry_after_ms")
            if isinstance(retry_after_ms, (int, float)) and retry_after_ms > 0:
                return retry_after_ms / 1000.0
        return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        client = await self._get_client()
        retries = self.max_retries if max_retries is None else max_retries
        delay = 1.0

        for attempt in range(1, retries + 2):
            try:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    timeout=timeout or self.timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt <= retries:
                    sleep_for = delay + random.uniform(0, 0.5)
                    logger.warning(
                        f"OpenCode request {method} {path} failed ({exc}), retrying in {sleep_for:.2f}s"
                    )
                    await asyncio.sleep(sleep_for)
                    delay = min(delay * 2, 30.0)
                    continue
                raise OpenCodeAPIError(
                    f"OpenCode request failed: {exc}",
                    status_code=None,
                ) from exc

            if response.status_code >= 400:
                payload = self._safe_json(response)
                retry_after = self._parse_retry_after(response, payload)
                rate_limited = response.status_code == 429
                if isinstance(payload, dict) and payload.get("errcode") == "M_LIMIT_EXCEEDED":
                    rate_limited = True

                if rate_limited:
                    if attempt <= retries:
                        sleep_for = retry_after if retry_after is not None else delay
                        sleep_for += random.uniform(0, 0.5)
                        logger.warning(
                            f"OpenCode rate limited on {method} {path}, retrying in {sleep_for:.2f}s"
                        )
                        await asyncio.sleep(sleep_for)
                        delay = min(delay * 2, 30.0)
                        continue
                    raise OpenCodeRateLimitError(
                        f"OpenCode rate limit exceeded for {method} {path}",
                        status_code=response.status_code,
                        payload=payload,
                        retry_after=retry_after,
                    )

                if response.status_code in {502, 503, 504} and attempt <= retries:
                    sleep_for = retry_after if retry_after is not None else delay
                    sleep_for += random.uniform(0, 0.5)
                    logger.warning(
                        f"OpenCode server error {response.status_code} on {method} {path}, "
                        f"retrying in {sleep_for:.2f}s"
                    )
                    await asyncio.sleep(sleep_for)
                    delay = min(delay * 2, 30.0)
                    continue

                raise OpenCodeAPIError(
                    f"OpenCode request failed with status {response.status_code}",
                    status_code=response.status_code,
                    payload=payload,
                    retry_after=retry_after,
                )

            return response

        raise OpenCodeAPIError(f"OpenCode request failed after retries for {method} {path}")

    def _extract_time(self, payload: dict[str, Any], key: str) -> str:
        time_block = payload.get("time") or {}
        value = None
        if isinstance(time_block, dict):
            value = time_block.get(key)
        if value is None:
            value = payload.get(f"{key}At")
        if value is None:
            value = payload.get(f"{key}_at")
        if value is None:
            value = payload.get(key)
        return str(value) if value is not None else ""

    def _require_field(self, payload: dict[str, Any], field: str, context: str) -> Any:
        if field not in payload:
            raise OpenCodeInvalidResponseError(f"Missing '{field}' in {context} payload")
        return payload[field]

    def _parse_session(self, payload: dict[str, Any]) -> Session:
        session_id = self._require_field(payload, "id", "session")
        return Session(
            id=session_id,
            title=payload.get("title"),
            parent_id=payload.get("parentID") or payload.get("parentId"),
            created_at=self._extract_time(payload, "created"),
            updated_at=self._extract_time(payload, "updated"),
            share=payload.get("share"),
            slug=payload.get("slug"),
            version=payload.get("version"),
            project_id=payload.get("projectID") or payload.get("projectId"),
            directory=payload.get("directory"),
        )

    def _parse_message(self, payload: dict[str, Any]) -> Message:
        info = payload.get("info")
        if not isinstance(info, dict):
            raise OpenCodeInvalidResponseError("Missing 'info' in message payload")
        message_id = self._require_field(info, "id", "message.info")
        session_id = info.get("sessionID") or info.get("sessionId")
        if not session_id:
            raise OpenCodeInvalidResponseError("Missing 'sessionID' in message.info payload")
        role = info.get("role")
        if not role:
            raise OpenCodeInvalidResponseError("Missing 'role' in message.info payload")
        return Message(
            id=message_id,
            session_id=session_id,
            role=role,
            created_at=self._extract_time(info, "created"),
            parts=payload.get("parts", []),
            agent=info.get("agent"),
            model=info.get("model"),
            parent_id=info.get("parentID") or info.get("parentId"),
        )

    async def health_check(self) -> dict[str, Any]:
        """Check server health.

        Returns:
            Health status including version
        """
        response = await self._request("GET", "/global/health")
        return response.json()

    async def get_config(self) -> dict[str, Any]:
        """Get server configuration.

        Returns:
            Server configuration
        """
        response = await self._request("GET", "/config")
        return response.json()

    async def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of sessions
        """
        response = await self._request("GET", "/session")
        data = response.json()
        if not isinstance(data, list):
            raise OpenCodeInvalidResponseError("Expected list for sessions response")
        return [self._parse_session(s) for s in data if isinstance(s, dict)]

    async def get_session_status(self) -> dict[str, SessionStatus]:
        """Get status for all sessions.

        Returns:
            Dictionary mapping session ID to status
        """
        response = await self._request("GET", "/session/status")
        data = response.json()
        if not isinstance(data, dict):
            raise OpenCodeInvalidResponseError("Expected dict for session status response")
        return {
            session_id: SessionStatus(
                session_id=session_id,
                status=status.get("status", "idle"),
                agent=status.get("agent"),
                model=status.get("model"),
            )
            for session_id, status in data.items()
        }

    async def create_session(
        self,
        title: str | None = None,
        parent_id: str | None = None,
    ) -> Session:
        """Create a new session.

        Args:
            title: Optional session title
            parent_id: Optional parent session ID

        Returns:
            Created session
        """
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if parent_id:
            body["parentID"] = parent_id
        response = await self._request("POST", "/session", json_body=body)
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenCodeInvalidResponseError("Expected dict for create session response")
        return self._parse_session(payload)

    async def get_session(self, session_id: str) -> Session:
        """Get session details.

        Args:
            session_id: Session ID

        Returns:
            Session details
        """
        response = await self._request("GET", f"/session/{session_id}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenCodeInvalidResponseError("Expected dict for get session response")
        return self._parse_session(payload)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID

        Returns:
            True if deleted successfully
        """
        response = await self._request("DELETE", f"/session/{session_id}")
        return response.json()

    async def abort_session(self, session_id: str) -> bool:
        """Abort a running session.

        Args:
            session_id: Session ID

        Returns:
            True if aborted successfully
        """
        response = await self._request("POST", f"/session/{session_id}/abort")
        return response.json()

    async def get_messages(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[Message]:
        """Get messages in a session.

        Args:
            session_id: Session ID
            limit: Maximum number of messages to return

        Returns:
            List of messages
        """
        params = {}
        if limit:
            params["limit"] = limit
        response = await self._request("GET", f"/session/{session_id}/message", params=params)
        data = response.json()
        if not isinstance(data, list):
            raise OpenCodeInvalidResponseError("Expected list for messages response")
        return [self._parse_message(m) for m in data if isinstance(m, dict)]

    async def send_message(
        self,
        session_id: str,
        content: str,
        agent: str | None = None,
        model: str | None = None,
    ) -> Message:
        """Send a message and wait for response.

        Args:
            session_id: Session ID
            content: Message content
            agent: Agent to use (build, plan, etc.)
            model: Model to use

        Returns:
            Response message
        """
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": content}],
        }
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model

        # Use longer timeout for message processing
        response = await self._request(
            "POST",
            f"/session/{session_id}/message",
            json_body=body,
            timeout=300.0,  # 5 minutes
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenCodeInvalidResponseError("Expected dict for send message response")
        return self._parse_message(payload)

    async def send_message_async(
        self,
        session_id: str,
        content: str,
        agent: str | None = None,
        model: str | None = None,
    ) -> None:
        """Send a message asynchronously (don't wait for response).

        Args:
            session_id: Session ID
            content: Message content
            agent: Agent to use
            model: Model to use
        """
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": content}],
        }
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model

        try:
            await self._request(
                "POST",
                f"/session/{session_id}/message",
                json_body=body,
                timeout=10.0,  # Short timeout since we're not waiting for full response
                max_retries=1,
            )
        except OpenCodeAPIError as exc:
            logger.warning(f"Error sending async message: {exc}")
            raise

    async def execute_command(
        self,
        session_id: str,
        command: str,
        arguments: str = "",
        agent: str | None = None,
        model: str | None = None,
    ) -> Message:
        """Execute a slash command.

        Args:
            session_id: Session ID
            command: Command name (without /)
            arguments: Command arguments
            agent: Agent to use
            model: Model to use

        Returns:
            Response message
        """
        body: dict[str, Any] = {
            "command": command,
            "arguments": arguments,
        }
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model

        response = await self._request(
            "POST",
            f"/session/{session_id}/command",
            json_body=body,
            timeout=300.0,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenCodeInvalidResponseError("Expected dict for command response")
        return self._parse_message(payload)

    async def list_agents(self) -> list[dict[str, Any]]:
        """List available agents.

        Returns:
            List of agent definitions
        """
        response = await self._request("GET", "/agent")
        payload = response.json()
        if not isinstance(payload, list):
            raise OpenCodeInvalidResponseError("Expected list for agents response")
        return payload

    async def list_commands(self) -> list[dict[str, Any]]:
        """List available commands.

        Returns:
            List of command definitions
        """
        response = await self._request("GET", "/command")
        payload = response.json()
        if not isinstance(payload, list):
            raise OpenCodeInvalidResponseError("Expected list for commands response")
        return payload

    async def subscribe_events(self) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to server-sent events.

        Yields:
            Event data dictionaries
        """
        client = await self._get_client()
        delay = 1.0
        while True:
            try:
                async with client.stream("GET", "/event") as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data:
                                try:
                                    yield json.loads(data)
                                except json.JSONDecodeError:
                                    logger.warning(f"Failed to parse event: {data}")
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                sleep_for = delay + random.uniform(0, 0.5)
                logger.warning(f"Event stream error: {exc}. Reconnecting in {sleep_for:.2f}s")
                await asyncio.sleep(sleep_for)
                delay = min(delay * 2, 30.0)

    async def get_diff(
        self, session_id: str, message_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get the diff for a session.

        Args:
            session_id: Session ID
            message_id: Optional message ID to get diff at

        Returns:
            List of file diffs
        """
        params = {}
        if message_id:
            params["messageID"] = message_id
        response = await self._request("GET", f"/session/{session_id}/diff", params=params)
        payload = response.json()
        if not isinstance(payload, list):
            raise OpenCodeInvalidResponseError("Expected list for diff response")
        return payload
