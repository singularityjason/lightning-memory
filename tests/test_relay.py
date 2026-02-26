"""Tests for relay WebSocket client (mocked)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightning_memory.relay import (
    RelayResponse,
    fetch_events,
    fetch_from_relays,
    publish_event,
    publish_to_relays,
)


def _mock_ws_connect(send_responses):
    """Create a mock websockets.connect context manager."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    recv_iter = iter(send_responses)
    ws.recv = AsyncMock(side_effect=lambda: next(recv_iter))

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestPublishEvent:
    def test_successful_publish(self):
        ok_response = json.dumps(["OK", "abc123", True, ""])
        mock_connect = _mock_ws_connect([ok_response])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            event = {"id": "abc123", "kind": 30078, "content": "test"}
            result = asyncio.run(publish_event("wss://test.relay", event))

        assert result.success is True
        assert result.relay == "wss://test.relay"

    def test_rejected_publish(self):
        ok_response = json.dumps(["OK", "abc123", False, "blocked: rate limit"])
        mock_connect = _mock_ws_connect([ok_response])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            event = {"id": "abc123", "kind": 30078, "content": "test"}
            result = asyncio.run(publish_event("wss://test.relay", event))

        assert result.success is False
        assert "rate limit" in result.message

    def test_connection_error(self):
        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(side_effect=ConnectionRefusedError("refused"))

            event = {"id": "abc123", "kind": 30078, "content": "test"}
            result = asyncio.run(publish_event("wss://bad.relay", event))

        assert result.success is False
        assert "refused" in result.message


class TestFetchEvents:
    def test_fetch_with_events(self):
        event1 = {"id": "e1", "kind": 30078, "content": "memory 1"}
        event2 = {"id": "e2", "kind": 30078, "content": "memory 2"}
        responses = [
            json.dumps(["EVENT", "sub1", event1]),
            json.dumps(["EVENT", "sub1", event2]),
            json.dumps(["EOSE", "sub1"]),
        ]
        mock_connect = _mock_ws_connect(responses)

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            result = asyncio.run(fetch_events("wss://test.relay", {"kinds": [30078]}))

        assert result.success is True
        assert len(result.events) == 2
        assert result.events[0]["id"] == "e1"

    def test_fetch_empty(self):
        responses = [json.dumps(["EOSE", "sub1"])]
        mock_connect = _mock_ws_connect(responses)

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            result = asyncio.run(fetch_events("wss://test.relay", {"kinds": [30078]}))

        assert result.success is True
        assert len(result.events) == 0

    def test_fetch_notice(self):
        responses = [json.dumps(["NOTICE", "rate limited"])]
        mock_connect = _mock_ws_connect(responses)

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            result = asyncio.run(fetch_events("wss://test.relay", {"kinds": [30078]}))

        assert result.success is False
        assert "rate limited" in result.message


class TestMultiRelay:
    def test_publish_to_multiple(self):
        ok_response = json.dumps(["OK", "abc123", True, ""])
        mock_connect = _mock_ws_connect([ok_response])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            event = {"id": "abc123", "kind": 30078, "content": "test"}
            results = asyncio.run(publish_to_relays(
                ["wss://r1.test", "wss://r2.test"], event
            ))

        assert len(results) == 2

    def test_fetch_from_multiple(self):
        responses = [json.dumps(["EOSE", "sub1"])]
        mock_connect = _mock_ws_connect(responses)

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)

            results = asyncio.run(fetch_from_relays(
                ["wss://r1.test", "wss://r2.test"],
                {"kinds": [30078]},
            ))

        assert len(results) == 2


class TestRelayResponse:
    def test_defaults(self):
        r = RelayResponse(relay="wss://test", success=True)
        assert r.events == []
        assert r.message == ""
