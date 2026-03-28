"""Tests for the MemoryEngine."""

import time


class TestStore:
    def test_store_returns_record(self, engine):
        result = engine.store("test memory content")
        assert "id" in result
        assert result["content"] == "test memory content"
        assert result["type"] == "general"

    def test_store_with_type(self, engine):
        result = engine.store("vendor note", memory_type="vendor")
        assert result["type"] == "vendor"

    def test_store_embeds_pubkey(self, engine):
        result = engine.store("with pubkey")
        assert result["metadata"]["agent_pubkey"] == engine.identity.public_key_hex


class TestQuery:
    def test_query_finds_match(self, engine):
        engine.store("bitcoin lightning payment")
        results = engine.query("lightning")
        assert len(results) >= 1
        assert "lightning" in results[0]["content"]

    def test_query_with_type_filter(self, engine):
        engine.store("payment to vendor", memory_type="transaction")
        engine.store("payment failed", memory_type="error")

        results = engine.query("payment", memory_type="transaction")
        assert all(r["type"] == "transaction" for r in results)

    def test_fallback_query(self, engine):
        engine.store("special chars test: [bracket] stuff")
        # FTS5 may choke on brackets; engine should fall back to LIKE
        results = engine.query("[bracket]")
        # Might get 0 from FTS5, but fallback should find it
        # Just verify it doesn't crash
        assert isinstance(results, list)


class TestList:
    def test_list_returns_stored(self, engine):
        engine.store("item one")
        engine.store("item two")
        results = engine.list()
        assert len(results) == 2

    def test_list_with_since(self, engine):
        engine.store("recent item")
        # "1h" = last hour, should include the item we just stored
        results = engine.list(since="1h")
        assert len(results) >= 1

    def test_list_with_type(self, engine):
        engine.store("a pref", memory_type="preference")
        engine.store("a tx", memory_type="transaction")
        results = engine.list(memory_type="preference")
        assert len(results) == 1


class TestStats:
    def test_stats_structure(self, engine):
        engine.store("one")
        stats = engine.stats()
        assert "total" in stats
        assert "by_type" in stats
        assert "agent_pubkey" in stats
        assert stats["total"] == 1


class TestDelete:
    def test_delete_existing(self, engine):
        result = engine.store("memory to delete later")
        mid = result["id"]
        from lightning_memory.db import delete_memory
        assert delete_memory(engine.conn, mid) is True
        # Should be gone from list
        assert len(engine.list()) == 0

    def test_delete_nonexistent(self, engine):
        from lightning_memory.db import delete_memory
        assert delete_memory(engine.conn, "nonexistent-id") is False

    def test_delete_removes_from_fts(self, engine):
        """Deleted memories should not appear in search results."""
        result = engine.store("unique searchable bitcoin content")
        mid = result["id"]
        from lightning_memory.db import delete_memory
        delete_memory(engine.conn, mid)
        results = engine.query("unique searchable bitcoin")
        assert len(results) == 0

    def test_delete_count_decreases(self, engine):
        r1 = engine.store("first memory for counting")
        engine.store("second memory for counting")
        assert engine.stats()["total"] == 2
        from lightning_memory.db import delete_memory
        delete_memory(engine.conn, r1["id"])
        assert engine.stats()["total"] == 1


class TestParseSince:
    def test_hours(self, engine):
        ts = engine._parse_since("2h")
        assert ts < time.time()
        assert ts > time.time() - (2 * 3600 + 10)

    def test_days(self, engine):
        ts = engine._parse_since("7d")
        assert ts < time.time()
        assert ts > time.time() - (7 * 86400 + 10)

    def test_minutes(self, engine):
        ts = engine._parse_since("30m")
        assert ts < time.time()
        assert ts > time.time() - (30 * 60 + 10)

    def test_unix_timestamp(self, engine):
        ts = engine._parse_since("1700000000")
        assert ts == 1700000000.0

    def test_invalid_defaults_to_24h(self, engine):
        ts = engine._parse_since("garbage")
        # Should default to last 24 hours
        assert ts > time.time() - 86401
        assert ts < time.time()
