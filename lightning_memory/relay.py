"""Nostr relay WebSocket client.

Implements NIP-01 message protocol for publishing and fetching events.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]


@dataclass
class RelayCircuitBreaker:
    """Per-relay circuit breaker to avoid blocking on down relays.

    After 3 consecutive failures, the circuit opens for an exponentially
    increasing backoff period (60s, 120s, ..., max 600s). Success resets.
    """

    failures: int = 0
    last_failure: float = 0.0
    open_until: float = 0.0

    def is_open(self) -> bool:
        return time.time() < self.open_until

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= 3:
            backoff = 60 * min(self.failures - 2, 10)  # 60s, 120s, ..., 600s max
            self.open_until = time.time() + backoff

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0


# Global circuit breaker state per relay URL
_circuit_breakers: dict[str, RelayCircuitBreaker] = {}


def _get_breaker(relay_url: str) -> RelayCircuitBreaker:
    if relay_url not in _circuit_breakers:
        _circuit_breakers[relay_url] = RelayCircuitBreaker()
    return _circuit_breakers[relay_url]


def reset_circuit_breakers() -> None:
    """Reset all circuit breakers (for testing)."""
    _circuit_breakers.clear()


@dataclass
class RelayResponse:
    """Result from a relay operation."""

    relay: str
    success: bool
    message: str = ""
    events: list[dict] = field(default_factory=list)


async def publish_event(relay_url: str, event: dict, timeout: float = 10.0) -> RelayResponse:
    """Publish a signed event to a single relay.

    Skips relays with an open circuit breaker.
    """
    breaker = _get_breaker(relay_url)
    if breaker.is_open():
        return RelayResponse(
            relay=relay_url, success=False, message="circuit_open",
        )

    if websockets is None:
        return RelayResponse(
            relay=relay_url, success=False,
            message="websockets not installed. pip install lightning-memory[sync]",
        )

    try:
        async with websockets.connect(relay_url, close_timeout=5, open_timeout=timeout) as ws:
            msg = json.dumps(["EVENT", event])
            await ws.send(msg)

            # Wait for OK response
            response = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(response)

            if isinstance(data, list) and len(data) >= 3 and data[0] == "OK":
                breaker.record_success()
                return RelayResponse(
                    relay=relay_url,
                    success=bool(data[2]),
                    message=data[3] if len(data) > 3 else "",
                )

            breaker.record_failure()
            return RelayResponse(
                relay=relay_url, success=False,
                message=f"Unexpected response: {data}",
            )
    except Exception as e:
        breaker.record_failure()
        return RelayResponse(relay=relay_url, success=False, message=str(e))


async def fetch_events(
    relay_url: str,
    filters: dict[str, Any],
    timeout: float = 10.0,
) -> RelayResponse:
    """Fetch events from a relay matching the given filter.

    Skips relays with an open circuit breaker.
    """
    breaker = _get_breaker(relay_url)
    if breaker.is_open():
        return RelayResponse(
            relay=relay_url, success=False, message="circuit_open",
        )

    if websockets is None:
        return RelayResponse(
            relay=relay_url, success=False,
            message="websockets not installed. pip install lightning-memory[sync]",
        )

    sub_id = uuid.uuid4().hex[:8]
    events: list[dict] = []

    try:
        async with websockets.connect(relay_url, close_timeout=5, open_timeout=timeout) as ws:
            req = json.dumps(["REQ", sub_id, filters])
            await ws.send(req)

            # Collect events until EOSE
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(raw)

                if isinstance(data, list):
                    if data[0] == "EVENT" and len(data) >= 3:
                        events.append(data[2])
                    elif data[0] == "EOSE":
                        break
                    elif data[0] == "NOTICE":
                        breaker.record_failure()
                        return RelayResponse(
                            relay=relay_url, success=False,
                            message=f"Relay notice: {data[1] if len(data) > 1 else ''}",
                        )

            # Close subscription
            await ws.send(json.dumps(["CLOSE", sub_id]))

        breaker.record_success()
        return RelayResponse(relay=relay_url, success=True, events=events)
    except Exception as e:
        breaker.record_failure()
        return RelayResponse(relay=relay_url, success=False, message=str(e))


async def publish_to_relays(
    relay_urls: list[str],
    event: dict,
    timeout: float = 10.0,
) -> list[RelayResponse]:
    """Publish an event to multiple relays concurrently."""
    tasks = [publish_event(url, event, timeout) for url in relay_urls]
    return list(await asyncio.gather(*tasks))


async def publish_batch_to_relays(
    relay_urls: list[str],
    events: list[dict],
    timeout: float = 10.0,
) -> list[tuple[dict, list[RelayResponse]]]:
    """Publish multiple events to multiple relays in a single event loop.

    Returns list of (event, [RelayResponse]) tuples — one per event.
    """
    results: list[tuple[dict, list[RelayResponse]]] = []
    for event in events:
        responses = await publish_to_relays(relay_urls, event, timeout)
        results.append((event, responses))
    return results


async def check_relay(relay_url: str, timeout: float = 5.0) -> RelayResponse:
    """Check if a relay is reachable by opening a WebSocket connection.

    Args:
        relay_url: WebSocket URL (wss://...)
        timeout: Connection timeout in seconds

    Returns:
        RelayResponse with success=True if the relay accepted the connection
    """
    if websockets is None:
        return RelayResponse(
            relay=relay_url, success=False,
            message="websockets not installed. pip install lightning-memory[sync]",
        )

    try:
        async with websockets.connect(relay_url, close_timeout=2, open_timeout=timeout) as ws:
            # Send a REQ and immediately close to verify the relay speaks NIP-01
            sub_id = uuid.uuid4().hex[:8]
            await ws.send(json.dumps(["REQ", sub_id, {"kinds": [30078], "limit": 0}]))
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            await ws.send(json.dumps(["CLOSE", sub_id]))
            data = json.loads(raw)
            if isinstance(data, list) and data[0] in ("EOSE", "EVENT", "NOTICE"):
                return RelayResponse(relay=relay_url, success=True, message="connected")
            return RelayResponse(relay=relay_url, success=True, message=f"response: {data[0]}")
    except Exception as e:
        return RelayResponse(relay=relay_url, success=False, message=str(e))


async def check_relays(relay_urls: list[str], timeout: float = 5.0) -> list[RelayResponse]:
    """Check multiple relays concurrently."""
    tasks = [check_relay(url, timeout) for url in relay_urls]
    return list(await asyncio.gather(*tasks))


async def fetch_from_relays(
    relay_urls: list[str],
    filters: dict[str, Any],
    timeout: float = 10.0,
) -> list[RelayResponse]:
    """Fetch events from multiple relays concurrently."""
    tasks = [fetch_events(url, filters, timeout) for url in relay_urls]
    return list(await asyncio.gather(*tasks))
