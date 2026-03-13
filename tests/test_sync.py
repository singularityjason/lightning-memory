"""Tests for the sync engine."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from lightning_memory.db import get_connection, store_memory
from lightning_memory.nostr import NIP85_KIND, NostrIdentity
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


class TestPullTrustAssertions:
    def test_pull_stores_attestations(self, sync_db, signing_identity):
        """Pulled NIP-85 events should be stored as attestation memories."""
        from lightning_memory.sync import pull_trust_assertions

        # First store a transaction so there's a known vendor
        store_memory(sync_db, "txn1", "Paid bitrefill", memory_type="transaction",
                     metadata={"vendor": "bitrefill.com", "amount_sats": 100})

        # Create a trust assertion event from a different "remote" identity
        remote = NostrIdentity.generate()
        event = remote.create_trust_assertion_event(
            vendor="bitrefill.com", score=0.9, basis="test", sign=remote.has_signing
        )
        # If can't sign, add a dummy sig for the test
        if "sig" not in event:
            event["sig"] = "0" * 128

        resp = RelayResponse(relay="wss://test", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 1

        # Verify stored as attestation
        row = sync_db.execute(
            "SELECT type, metadata FROM memories WHERE nostr_event_id = ?", (event["id"],)
        ).fetchone()
        assert row is not None
        assert row["type"] == "attestation"
        meta = json.loads(row["metadata"])
        assert meta["vendor"] == "bitrefill.com"
        assert meta["trust_score"] == 0.9

    def test_pull_rejects_out_of_range_score(self, sync_db, signing_identity):
        """Events with score > 1.0 should be skipped."""
        from lightning_memory.sync import pull_trust_assertions
        import hashlib

        # Store a transaction so vendor exists
        store_memory(sync_db, "txn2", "Paid bad.com", memory_type="transaction",
                     metadata={"vendor": "bad.com"})

        # Manually craft event with bad score
        remote = NostrIdentity.generate()
        content = json.dumps({"vendor": "bad.com", "score": 5.0, "basis": "fake"})
        tags = [["d", "trust:bad.com"]]
        event = {
            "kind": NIP85_KIND,
            "pubkey": remote.public_key_hex,
            "created_at": 1710000000,
            "tags": tags,
            "content": content,
        }
        serialized = json.dumps(
            [0, event["pubkey"], event["created_at"], event["kind"],
             event["tags"], event["content"]],
            separators=(",", ":"), ensure_ascii=False,
        )
        event["id"] = hashlib.sha256(serialized.encode()).hexdigest()
        event["sig"] = "0" * 128

        resp = RelayResponse(relay="wss://test", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 0

    def test_pull_deduplicates(self, sync_db, signing_identity):
        """Same event from multiple relays should be stored once."""
        from lightning_memory.sync import pull_trust_assertions

        # Store transaction for vendor
        store_memory(sync_db, "txn3", "Paid v.com", memory_type="transaction",
                     metadata={"vendor": "v.com"})

        remote = NostrIdentity.generate()
        event = remote.create_trust_assertion_event("v.com", 0.8, sign=remote.has_signing)
        if "sig" not in event:
            event["sig"] = "0" * 128

        resp1 = RelayResponse(relay="wss://r1", success=True, events=[event])
        resp2 = RelayResponse(relay="wss://r2", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp1, resp2]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://r1", "wss://r2"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 1


class TestPushTrustAssertion:
    def test_push_succeeds(self, sync_db, signing_identity):
        """Should create and publish a NIP-85 event."""
        from lightning_memory.sync import push_trust_assertion

        ok = RelayResponse(relay="wss://test", success=True)
        with patch("lightning_memory.sync.publish_to_relays", new_callable=AsyncMock) as mock_pub:
            mock_pub.return_value = [ok]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                result = push_trust_assertion(
                    sync_db, signing_identity, "bitrefill.com", 0.9, "transaction_history"
                )

        assert result.pushed == 1
        assert result.errors == []

        # Verify locally stored attestation
        row = sync_db.execute(
            "SELECT type, metadata FROM memories WHERE type = 'attestation'"
        ).fetchone()
        assert row is not None
        meta = json.loads(row["metadata"])
        assert meta["vendor"] == "bitrefill.com"
        assert meta["trust_score"] == 0.9

    def test_push_without_signing(self, sync_db):
        """Fallback identity can't push."""
        from lightning_memory.sync import push_trust_assertion
        import os, hashlib
        privkey = os.urandom(32)
        pubkey = hashlib.sha256(privkey).digest()
        identity = NostrIdentity(private_key=privkey, public_key=pubkey)

        result = push_trust_assertion(sync_db, identity, "x.com", 0.5)
        assert result.pushed == 0
        assert len(result.errors) == 1
        assert "secp256k1" in result.errors[0]


def test_pull_skips_kya_events(sync_db, signing_identity):
    """pull_memories should skip events with type:kya tag."""
    import hashlib

    event = signing_identity.create_memory_event(
        "KYA attestation", "general", "kya1", sign=True
    )
    # Add type:kya tag
    event["tags"].append(["type", "kya"])
    # Recalculate event ID after modifying tags
    serialized = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"],
         event["tags"], event["content"]],
        separators=(",", ":"), ensure_ascii=False,
    )
    event["id"] = hashlib.sha256(serialized.encode()).hexdigest()
    # Re-sign
    if signing_identity.has_signing:
        signing_identity.sign_event(event)

    resp = RelayResponse(relay="wss://test", success=True, events=[event])

    with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = [resp]
        with patch("lightning_memory.sync.load_config") as mock_cfg:
            mock_cfg.return_value.relays = ["wss://test"]
            mock_cfg.return_value.sync_timeout_seconds = 5
            mock_cfg.return_value.max_events_per_sync = 100
            result = pull_memories(sync_db, signing_identity)

    assert result.pulled == 0  # Skipped due to type:kya tag


def test_push_gateway_announcement(tmp_db, signing_identity):
    """push_gateway_announcement should create and publish gateway event."""
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import push_gateway_announcement

    mock_responses = [MagicMock(success=True)]
    with patch("lightning_memory.sync.publish_to_relays", return_value=mock_responses) as mock_pub:
        with patch("lightning_memory.sync.load_config") as mock_cfg:
            mock_cfg.return_value.relays = ["wss://test"]
            mock_cfg.return_value.sync_timeout_seconds = 5
            mock_cfg.return_value.pricing = {"memory_query": 2}
            result = push_gateway_announcement(
                tmp_db, signing_identity,
                gateway_url="https://gw.example.com",
                operations={"memory_query": 2},
            )
    assert result.pushed == 1
    mock_pub.assert_called_once()
    # Verify the event passed to publish has type:gateway tag
    event = mock_pub.call_args[0][1]
    type_tags = [t for t in event["tags"] if t[0] == "type"]
    assert type_tags[0][1] == "gateway"


def test_pull_gateway_announcements(tmp_db, tmp_identity):
    """pull_gateway_announcements should store gateways from relay events."""
    import json
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import pull_gateway_announcements

    fake_event = {
        "id": "abc123" * 10 + "abcd",
        "kind": 30078,
        "pubkey": "beef" * 16,
        "created_at": 1700000000,
        "tags": [
            ["d", "gateway:" + "beef" * 16],
            ["type", "gateway"],
        ],
        "content": json.dumps({
            "url": "https://remote-gw.example.com",
            "operations": {"memory_query": 3},
            "relays": ["wss://relay.damus.io"],
            "version": "0.6.0",
        }),
    }
    mock_resp = MagicMock(success=True, events=[fake_event])
    with patch("lightning_memory.sync.fetch_from_relays", return_value=[mock_resp]):
        result = pull_gateway_announcements(tmp_db, tmp_identity)

    assert result.pulled == 1
    row = tmp_db.execute("SELECT * FROM known_gateways WHERE agent_pubkey = ?", ("beef" * 16,)).fetchone()
    assert row is not None
    assert row["url"] == "https://remote-gw.example.com"


def test_pull_gateway_announcements_updates_existing(tmp_db, tmp_identity):
    """pull_gateway_announcements should update existing gateway entries."""
    import json, time
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import pull_gateway_announcements

    now = time.time()
    tmp_db.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("beef" * 16, "https://old-url.com", "{}", "[]", now, now),
    )
    tmp_db.commit()

    fake_event = {
        "id": "def456" * 10 + "defg",
        "kind": 30078,
        "pubkey": "beef" * 16,
        "created_at": 1700000000,
        "tags": [["d", "gateway:" + "beef" * 16], ["type", "gateway"]],
        "content": json.dumps({
            "url": "https://new-url.com",
            "operations": {"memory_query": 5},
            "relays": [],
            "version": "0.6.0",
        }),
    }
    mock_resp = MagicMock(success=True, events=[fake_event])
    with patch("lightning_memory.sync.fetch_from_relays", return_value=[mock_resp]):
        result = pull_gateway_announcements(tmp_db, tmp_identity)

    assert result.pulled == 1
    row = tmp_db.execute("SELECT * FROM known_gateways WHERE agent_pubkey = ?", ("beef" * 16,)).fetchone()
    assert row["url"] == "https://new-url.com"
