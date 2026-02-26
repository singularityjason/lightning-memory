"""Nostr relay WebSocket client.

Implements NIP-01 message protocol for publishing and fetching events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]


@dataclass
class RelayResponse:
    """Result from a relay operation."""

    relay: str
    success: bool
    message: str = ""
    events: list[dict] = field(default_factory=list)


async def publish_event(relay_url: str, event: dict, timeout: float = 10.0) -> RelayResponse:
    """Publish a signed event to a single relay.

    Args:
        relay_url: WebSocket URL (wss://...)
        event: Signed Nostr event dict (must have 'sig' field)
        timeout: Connection timeout in seconds

    Returns:
        RelayResponse with success status and relay message
    """
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
                return RelayResponse(
                    relay=relay_url,
                    success=bool(data[2]),
                    message=data[3] if len(data) > 3 else "",
                )

            return RelayResponse(
                relay=relay_url, success=False,
                message=f"Unexpected response: {data}",
            )
    except Exception as e:
        return RelayResponse(relay=relay_url, success=False, message=str(e))


async def fetch_events(
    relay_url: str,
    filters: dict[str, Any],
    timeout: float = 10.0,
) -> RelayResponse:
    """Fetch events from a relay matching the given filter.

    Args:
        relay_url: WebSocket URL
        filters: NIP-01 filter dict (kinds, authors, #d, since, until, limit)
        timeout: Connection timeout

    Returns:
        RelayResponse with matched events
    """
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
                        return RelayResponse(
                            relay=relay_url, success=False,
                            message=f"Relay notice: {data[1] if len(data) > 1 else ''}",
                        )

            # Close subscription
            await ws.send(json.dumps(["CLOSE", sub_id]))

        return RelayResponse(relay=relay_url, success=True, events=events)
    except Exception as e:
        return RelayResponse(relay=relay_url, success=False, message=str(e))


async def publish_to_relays(
    relay_urls: list[str],
    event: dict,
    timeout: float = 10.0,
) -> list[RelayResponse]:
    """Publish an event to multiple relays concurrently."""
    tasks = [publish_event(url, event, timeout) for url in relay_urls]
    return list(await asyncio.gather(*tasks))


async def fetch_from_relays(
    relay_urls: list[str],
    filters: dict[str, Any],
    timeout: float = 10.0,
) -> list[RelayResponse]:
    """Fetch events from multiple relays concurrently."""
    tasks = [fetch_events(url, filters, timeout) for url in relay_urls]
    return list(await asyncio.gather(*tasks))
