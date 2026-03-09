"""Integration tests for Nostr relay sync with a mock relay server."""

import asyncio
import json
import threading
import time
from unittest.mock import patch

import pytest

try:
    import websockets
    import websockets.server
except ImportError:
    websockets = None

from lightning_memory.config import Config, reset_cache
from lightning_memory.db import get_connection, store_memory
from lightning_memory.nostr import KIND_NIP78, NostrIdentity
from lightning_memory.relay import RelayResponse
from lightning_memory.sync import (
    SyncResult,
    _ensure_sync_schema,
    _get_cursor,
    export_memories,
    pull_memories,
    push_memories,
)

pytestmark = pytest.mark.skipif(websockets is None, reason="websockets not installed")


class MockRelay:
    """Lightweight mock Nostr relay for integration testing."""

    def __init__(self):
        self.events: dict[str, dict] = {}  # event_id -> event
        self.server = None
        self.port = None
        self._stop_event = threading.Event()

    async def handler(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "EVENT":
                event = msg[1]
                self.events[event["id"]] = event
                await ws.send(json.dumps(["OK", event["id"], True, ""]))
            elif msg[0] == "REQ":
                sub_id = msg[1]
                filters = msg[2] if len(msg) > 2 else {}
                for event in self._match(filters):
                    await ws.send(json.dumps(["EVENT", sub_id, event]))
                await ws.send(json.dumps(["EOSE", sub_id]))
            elif msg[0] == "CLOSE":
                pass

    def _match(self, filters: dict) -> list[dict]:
        """Filter stored events by NIP-01 filter."""
        results = []
        for event in self.events.values():
            if "kinds" in filters and event.get("kind") not in filters["kinds"]:
                continue
            if "authors" in filters and event.get("pubkey") not in filters["authors"]:
                continue
            if "since" in filters and event.get("created_at", 0) < filters["since"]:
                continue
            results.append(event)
        limit = filters.get("limit")
        if limit and limit > 0:
            results = results[:limit]
        return results

    async def _run(self):
        async with websockets.server.serve(self.handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await asyncio.Future()  # run forever

    def start(self):
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_in_thread, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_in_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run())

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@pytest.fixture
def mock_relay():
    relay = MockRelay()
    relay.start()
    yield relay


@pytest.fixture
def signing_identity():
    try:
        import secp256k1  # noqa: F401
    except ImportError:
        pytest.skip("secp256k1 not installed")
    return NostrIdentity.generate()


@pytest.fixture
def sync_db():
    conn = get_connection(":memory:")
    _ensure_sync_schema(conn)
    yield conn
    conn.close()


def _patch_config(relay_url: str):
    """Return a mock config pointing at the mock relay."""
    config = Config(
        relays=[relay_url],
        sync_timeout_seconds=5,
        max_events_per_sync=100,
    )
    return patch("lightning_memory.sync.load_config", return_value=config)


class TestRoundtripPushPull:
    def test_push_then_pull_on_second_db(self, sync_db, signing_identity, mock_relay):
        """Store locally, push to relay, pull into a fresh DB."""
        # Store memories locally
        store_memory(sync_db, "m1", "payment to bitrefill 500 sats", memory_type="transaction")
        store_memory(sync_db, "m2", "always use early returns", memory_type="preference")

        # Push to mock relay
        with _patch_config(mock_relay.url):
            push_result = push_memories(sync_db, signing_identity)

        assert push_result.pushed == 2
        assert push_result.errors == []
        assert len(mock_relay.events) == 2

        # Create a second DB and pull
        conn2 = get_connection(":memory:")
        _ensure_sync_schema(conn2)

        with _patch_config(mock_relay.url):
            pull_result = pull_memories(conn2, signing_identity)

        assert pull_result.pulled == 2

        rows = conn2.execute("SELECT content FROM memories ORDER BY content").fetchall()
        contents = [r[0] for r in rows]
        assert "always use early returns" in contents
        assert "payment to bitrefill 500 sats" in contents
        conn2.close()


class TestPushSyncLog:
    def test_push_tracks_entries(self, sync_db, signing_identity, mock_relay):
        store_memory(sync_db, "m1", "test memory")

        with _patch_config(mock_relay.url):
            push_memories(sync_db, signing_identity)

        rows = sync_db.execute("SELECT memory_id, relay_count FROM sync_log").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "m1"
        assert rows[0][1] >= 1

    def test_push_no_duplicates(self, sync_db, signing_identity, mock_relay):
        store_memory(sync_db, "m1", "test memory")

        with _patch_config(mock_relay.url):
            r1 = push_memories(sync_db, signing_identity)
            r2 = push_memories(sync_db, signing_identity)

        assert r1.pushed == 1
        assert r2.pushed == 0  # already synced


class TestPullDedup:
    def test_dedup_same_relay_twice(self, sync_db, signing_identity, mock_relay):
        """Config with same relay listed twice should still only import once."""
        event = signing_identity.create_memory_event("dup test", "general", "dup1", sign=True)
        mock_relay.events[event["id"]] = event

        config = Config(
            relays=[mock_relay.url, mock_relay.url],
            sync_timeout_seconds=5,
            max_events_per_sync=100,
        )
        with patch("lightning_memory.sync.load_config", return_value=config):
            result = pull_memories(sync_db, signing_identity)

        assert result.pulled == 1


class TestPullCursor:
    def test_cursor_updated_after_pull(self, sync_db, signing_identity, mock_relay):
        event = signing_identity.create_memory_event("first", "general", "c1", sign=True)
        mock_relay.events[event["id"]] = event

        with _patch_config(mock_relay.url):
            pull_memories(sync_db, signing_identity)

        cursor = _get_cursor(sync_db, "last_pull_timestamp")
        assert cursor is not None
        assert float(cursor) > 0

    def test_second_pull_only_gets_new(self, sync_db, signing_identity, mock_relay):
        event1 = signing_identity.create_memory_event("first", "general", "c1", sign=True)
        mock_relay.events[event1["id"]] = event1

        with _patch_config(mock_relay.url):
            r1 = pull_memories(sync_db, signing_identity)

        assert r1.pulled == 1

        # Add a new event with a later timestamp
        time.sleep(0.1)
        event2 = signing_identity.create_memory_event("second", "general", "c2", sign=True)
        mock_relay.events[event2["id"]] = event2

        with _patch_config(mock_relay.url):
            r2 = pull_memories(sync_db, signing_identity)

        assert r2.pulled == 1  # only the new one


class TestPushRequiresSigning:
    def test_no_secp256k1(self, sync_db):
        import hashlib
        import os

        privkey = os.urandom(32)
        pubkey = hashlib.sha256(privkey).digest()
        identity = NostrIdentity(private_key=privkey, public_key=pubkey)

        store_memory(sync_db, "m1", "can't push this")
        result = push_memories(sync_db, identity)

        assert result.pushed == 0
        assert len(result.errors) == 1
        assert "secp256k1" in result.errors[0]


class TestExportNIP78:
    def test_export_structure(self, sync_db, signing_identity):
        store_memory(sync_db, "e1", "test export", memory_type="transaction")

        events = export_memories(sync_db, signing_identity, limit=10)
        assert len(events) == 1

        event = events[0]
        assert event["kind"] == KIND_NIP78
        assert event["content"] == "test export"
        assert event["pubkey"] == signing_identity.public_key_hex

        # Verify tags
        tags = {t[0]: t[1] for t in event["tags"] if len(t) >= 2}
        assert "d" in tags  # NIP-78 addressable
        assert tags["d"].startswith("lm:")
        assert tags.get("t") == "transaction"
        assert tags.get("client") == "lightning-memory"

        # Signed events have sig
        if signing_identity.has_signing:
            assert "sig" in event
            assert len(event["sig"]) == 128  # 64 bytes hex
