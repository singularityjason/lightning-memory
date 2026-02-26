"""Tests for the sync engine."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from lightning_memory.db import get_connection, store_memory
from lightning_memory.nostr import NostrIdentity
from lightning_memory.relay import RelayResponse
from lightning_memory.sync import (
    SyncResult,
    _ensure_sync_schema,
    _extract_memory_id,
    _extract_tag,
    export_memories,
    pull_memories,
    push_memories,
)


@pytest.fixture
def sync_db():
    conn = get_connection(":memory:")
    _ensure_sync_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def signing_identity():
    try:
        import secp256k1  # noqa: F401
    except ImportError:
        pytest.skip("secp256k1 not installed")
    return NostrIdentity.generate()


class TestSyncSchema:
    def test_tables_created(self, sync_db):
        tables = {
            row[0]
            for row in sync_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sync_log" in tables
        assert "sync_cursor" in tables


class TestPushMemories:
    def test_push_with_no_memories(self, sync_db, signing_identity):
        result = push_memories(sync_db, signing_identity)
        assert result.pushed == 0
        assert result.errors == []

    def test_push_skips_already_synced(self, sync_db, signing_identity):
        store_memory(sync_db, "m1", "test memory")

        # Simulate already synced
        sync_db.execute(
            "INSERT INTO sync_log (memory_id, event_id, pushed_at, relay_count) VALUES (?, ?, ?, ?)",
            ("m1", "e1", 1000.0, 1),
        )
        sync_db.commit()

        result = push_memories(sync_db, signing_identity)
        assert result.pushed == 0

    def test_push_succeeds(self, sync_db, signing_identity):
        store_memory(sync_db, "m2", "push this memory")

        ok = RelayResponse(relay="wss://test", success=True)
        with patch("lightning_memory.sync.publish_to_relays", new_callable=AsyncMock) as mock_pub:
            mock_pub.return_value = [ok]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = push_memories(sync_db, signing_identity)

        assert result.pushed == 1
        assert result.errors == []

        # Verify sync_log entry
        row = sync_db.execute("SELECT * FROM sync_log WHERE memory_id = ?", ("m2",)).fetchone()
        assert row is not None

    def test_push_without_signing(self, sync_db):
        """Fallback identity can't push."""
        import os, hashlib
        privkey = os.urandom(32)
        pubkey = hashlib.sha256(privkey).digest()
        identity = NostrIdentity(private_key=privkey, public_key=pubkey)

        store_memory(sync_db, "m3", "can't push this")
        result = push_memories(sync_db, identity)
        assert result.pushed == 0
        assert len(result.errors) == 1
        assert "secp256k1" in result.errors[0]


class TestPullMemories:
    def test_pull_new_events(self, sync_db, signing_identity):
        event = signing_identity.create_memory_event(
            "remote memory", "general", "remote1", sign=True
        )
        resp = RelayResponse(relay="wss://test", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_memories(sync_db, signing_identity)

        assert result.pulled == 1

        # Verify memory was inserted
        row = sync_db.execute("SELECT content FROM memories WHERE nostr_event_id = ?", (event["id"],)).fetchone()
        assert row is not None
        assert row[0] == "remote memory"

    def test_pull_deduplicates(self, sync_db, signing_identity):
        event = signing_identity.create_memory_event(
            "dedup test", "general", "dup1", sign=True
        )
        # Same event from two relays
        resp1 = RelayResponse(relay="wss://r1", success=True, events=[event])
        resp2 = RelayResponse(relay="wss://r2", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp1, resp2]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://r1", "wss://r2"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_memories(sync_db, signing_identity)

        assert result.pulled == 1  # Only inserted once

    def test_pull_skips_existing(self, sync_db, signing_identity):
        event = signing_identity.create_memory_event(
            "already here", "general", "exist1", sign=True
        )
        # Pre-insert the memory with this event ID
        store_memory(sync_db, "exist1", "already here", nostr_event_id=event["id"])

        resp = RelayResponse(relay="wss://test", success=True, events=[event])
        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_memories(sync_db, signing_identity)

        assert result.pulled == 0


class TestExportMemories:
    def test_export(self, sync_db):
        identity = NostrIdentity.generate()
        store_memory(sync_db, "exp1", "export me", memory_type="transaction")
        store_memory(sync_db, "exp2", "export me too", memory_type="vendor")

        events = export_memories(sync_db, identity, limit=10)
        assert len(events) == 2
        assert all("id" in e for e in events)
        assert all("kind" in e for e in events)

    def test_export_limit(self, sync_db):
        identity = NostrIdentity.generate()
        for i in range(5):
            store_memory(sync_db, f"lim{i}", f"memory {i}")

        events = export_memories(sync_db, identity, limit=2)
        assert len(events) == 2


class TestHelpers:
    def test_extract_memory_id_with_prefix(self):
        event = {"tags": [["d", "lm:abc123"]]}
        assert _extract_memory_id(event) == "abc123"

    def test_extract_memory_id_fallback(self):
        event = {"id": "deadbeef12345678", "tags": []}
        assert _extract_memory_id(event) == "deadbeef12345678"

    def test_extract_tag(self):
        event = {"tags": [["t", "transaction"], ["d", "lm:xyz"]]}
        assert _extract_tag(event, "t") == "transaction"
        assert _extract_tag(event, "d") == "lm:xyz"
        assert _extract_tag(event, "missing") is None

    def test_sync_result_to_dict(self):
        r = SyncResult(pushed=3, pulled=5, errors=["err1"])
        d = r.to_dict()
        assert d["pushed"] == 3
        assert d["pulled"] == 5
        assert d["errors"] == ["err1"]
