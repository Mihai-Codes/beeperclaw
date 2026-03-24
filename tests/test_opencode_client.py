"""Unit tests for the OpenCode client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest

from beeperclaw.opencode_client import (
    Message,
    OpenCodeClient,
    OpenCodeInvalidResponseError,
    PromptAttachment,
)


@pytest.fixture
def client() -> OpenCodeClient:
    """Create a test client."""
    return OpenCodeClient(base_url="http://127.0.0.1:4096")


def _assistant_message_payload(
    *, message_id: str = "msg-1", session_id: str = "sess-1", text: str = "Finished task"
) -> dict[str, object]:
    return {
        "id": message_id,
        "info": {
            "id": message_id,
            "sessionID": session_id,
            "role": "assistant",
            "parts": [{"type": "text", "text": text}],
        },
    }


class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://127.0.0.1:4096")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeHttpClient:
    def __init__(self, responses: list[_FakeStreamResponse]) -> None:
        self._responses = responses
        self.requested_paths: list[str] = []

    def stream(self, method: str, path: str) -> _FakeStreamResponse:
        self.requested_paths.append(path)
        return self._responses.pop(0)


class _RetryingHttpClient:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = responses
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls += 1
        self.requests.append({"method": method, "path": path, **kwargs})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestOpenCodeClient:
    """Tests for OpenCodeClient."""

    def test_normalize_documented_event_shape(self, client: OpenCodeClient) -> None:
        event = client.normalize_event(
            {
                "type": "session.message",
                "properties": {
                    "sessionID": "sess-1",
                    "message": _assistant_message_payload(),
                },
            }
        )

        assert event.type == "session.message"
        assert event.properties["sessionID"] == "sess-1"
        assert "type" not in event.properties

    def test_normalize_legacy_event_shape(self, client: OpenCodeClient) -> None:
        event = client.normalize_event(
            {
                "type": "session.message",
                "sessionID": "sess-1",
                "message": _assistant_message_payload(),
            }
        )

        assert event.type == "session.message"
        assert event.properties["sessionID"] == "sess-1"

    def test_extract_assistant_message_and_text(self, client: OpenCodeClient) -> None:
        event = client.normalize_event(
            {
                "type": "session.message",
                "sessionID": "sess-1",
                "message": _assistant_message_payload(text="Line one\nLine two"),
            }
        )

        message = client.extract_assistant_message_from_event(event)

        assert isinstance(message, Message)
        assert message.session_id == "sess-1"
        assert client.get_message_text(message) == "Line one\nLine two"

    @pytest.mark.asyncio
    async def test_subscribe_events_falls_back_to_legacy_path(
        self, client: OpenCodeClient
    ) -> None:
        fake_client = _FakeHttpClient(
            [
                _FakeStreamResponse(status_code=404, lines=[]),
                _FakeStreamResponse(
                    status_code=200,
                    lines=[
                        "data: "
                        + json.dumps(
                            {
                                "type": "session.message",
                                "sessionID": "sess-1",
                                "message": _assistant_message_payload(),
                            }
                        )
                    ],
                ),
            ]
        )
        client._get_client = AsyncMock(return_value=fake_client)  # type: ignore[attr-defined]

        events = []
        async for event in client.subscribe_events():
            events.append(event)
            break

        assert fake_client.requested_paths == ["/global/event", "/event"]
        assert events[0].type == "session.message"
        assert client.extract_session_id_from_event(events[0]) == "sess-1"

    @pytest.mark.asyncio
    async def test_request_retries_transport_error(self, client: OpenCodeClient, monkeypatch) -> None:
        request = httpx.Request("GET", "http://127.0.0.1:4096/session")
        fake_client = _RetryingHttpClient(
            [
                httpx.ConnectError("boom", request=request),
                httpx.Response(200, request=request, json=[]),
            ]
        )
        client._get_client = AsyncMock(return_value=fake_client)  # type: ignore[attr-defined]
        sleep = AsyncMock()
        monkeypatch.setattr("beeperclaw.opencode_client.asyncio.sleep", sleep)
        monkeypatch.setattr("beeperclaw.opencode_client.random.uniform", lambda _a, _b: 0.0)

        sessions = await client.list_sessions()

        assert sessions == []
        assert fake_client.calls == 2
        sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_sessions_rejects_non_json_payload(self, client: OpenCodeClient) -> None:
        request = httpx.Request("GET", "http://127.0.0.1:4096/session")
        fake_client = _RetryingHttpClient(
            [
                httpx.Response(200, request=request, text="not-json"),
            ]
        )
        client._get_client = AsyncMock(return_value=fake_client)  # type: ignore[attr-defined]

        with pytest.raises(OpenCodeInvalidResponseError):
            await client.list_sessions()

    @pytest.mark.asyncio
    async def test_send_message_async_includes_attachment_context(
        self, client: OpenCodeClient, tmp_path
    ) -> None:
        request = httpx.Request("POST", "http://127.0.0.1:4096/session/sess-1/message")
        fake_client = _RetryingHttpClient(
            [
                httpx.Response(200, request=request, json={}),
            ]
        )
        client._get_client = AsyncMock(return_value=fake_client)  # type: ignore[attr-defined]
        attachment_path = tmp_path / "error.png"
        attachment = PromptAttachment(
            path=str(attachment_path),
            mime="image/png",
            filename="error.png",
            caption="Screenshot of the failure",
        )

        await client.send_message_async(
            session_id="sess-1",
            content="Investigate this",
            agent="plan",
            attachments=[attachment],
        )

        body = fake_client.requests[0]["json"]
        assert isinstance(body, dict)
        parts = body["parts"]
        assert parts[0]["type"] == "text"
        assert "Attachment context:" in parts[0]["text"]
        assert "Screenshot of the failure" in parts[0]["text"]
        assert parts[1]["type"] == "file"
        assert parts[1]["filename"] == "error.png"
        assert parts[1]["url"] == attachment_path.resolve().as_uri()
