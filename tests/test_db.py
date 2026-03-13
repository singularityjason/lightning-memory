"""Tests for the SQLite + FTS5 storage layer."""

from lightning_memory.db import (
    count_memories,
    delete_memory,
    list_memories,
    query_memories,
    store_memory,
)


class TestSchema:
    def test_tables_created(self, tmp_db):
        tables = {
            row[0]
            for row in tmp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memories" in tables
        assert "memories_fts" in tables

    def test_indexes_created(self, tmp_db):
        indexes = {
            row[0]
            for row in tmp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_memories_type" in indexes
        assert "idx_memories_created" in indexes
        assert "idx_memories_nostr" in indexes


def test_budget_rules_table_exists(tmp_db):
    """Budget rules table should be created by schema init."""
    row = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_rules'"
    ).fetchone()
    assert row is not None, "budget_rules table should exist"


def test_vendor_kyc_table_exists(tmp_db):
    """Vendor KYC table should be created by schema init."""
    row = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vendor_kyc'"
    ).fetchone()
    assert row is not None, "vendor_kyc table should exist"


def test_agent_attestations_table_exists(tmp_db):
    """Schema should include agent_attestations table."""
    tables = {row[0] for row in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "agent_attestations" in tables


def test_auth_sessions_table_exists(tmp_db):
    """Schema should include auth_sessions table."""
    tables = {row[0] for row in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "auth_sessions" in tables


class TestStore:
    def test_store_basic(self, tmp_db):
        result = store_memory(tmp_db, "id1", "hello world")
        assert result["id"] == "id1"
        assert result["content"] == "hello world"
        assert result["type"] == "general"
        assert "created_at" in result

    def test_store_with_type_and_metadata(self, tmp_db):
        result = store_memory(
            tmp_db,
            "id2",
            "paid 100 sats",
            memory_type="transaction",
            metadata={"vendor": "example.com", "amount_sats": 100},
        )
        assert result["type"] == "transaction"
        assert result["metadata"]["vendor"] == "example.com"

    def test_upsert_updates_content(self, tmp_db):
        store_memory(tmp_db, "id3", "original content")
        store_memory(tmp_db, "id3", "updated content")

        rows = tmp_db.execute(
            "SELECT content FROM memories WHERE id = ?", ("id3",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "updated content"


class TestFTS5Query:
    def test_basic_query(self, tmp_db):
        store_memory(tmp_db, "a1", "bitcoin lightning payment")
        store_memory(tmp_db, "a2", "ethereum smart contract")
        store_memory(tmp_db, "a3", "bitcoin mining hardware")

        results = query_memories(tmp_db, "bitcoin")
        assert len(results) >= 2
        assert all("bitcoin" in r["content"] for r in results)

    def test_bm25_ranking(self, tmp_db):
        store_memory(tmp_db, "b1", "lightning network payment channel")
        store_memory(tmp_db, "b2", "payment received from vendor")

        # FTS5 phrase match: query wraps in quotes, so use a single term
        results = query_memories(tmp_db, "payment")
        assert len(results) == 2
        assert all(r["relevance"] > 0 for r in results)

    def test_query_with_type_filter(self, tmp_db):
        store_memory(tmp_db, "c1", "payment to vendor", memory_type="transaction")
        store_memory(tmp_db, "c2", "payment error log", memory_type="error")

        results = query_memories(tmp_db, "payment", memory_type="transaction")
        assert len(results) == 1
        assert results[0]["type"] == "transaction"


class TestList:
    def test_list_all(self, tmp_db):
        store_memory(tmp_db, "d1", "first")
        store_memory(tmp_db, "d2", "second")
        results = list_memories(tmp_db)
        assert len(results) == 2

    def test_list_by_type(self, tmp_db):
        store_memory(tmp_db, "e1", "a vendor note", memory_type="vendor")
        store_memory(tmp_db, "e2", "a preference", memory_type="preference")
        results = list_memories(tmp_db, memory_type="vendor")
        assert len(results) == 1
        assert results[0]["type"] == "vendor"

    def test_list_with_since(self, tmp_db):
        import time

        store_memory(tmp_db, "f1", "old memory")
        future = time.time() + 1000
        results = list_memories(tmp_db, since=future)
        assert len(results) == 0

    def test_list_limit(self, tmp_db):
        for i in range(10):
            store_memory(tmp_db, f"g{i}", f"memory {i}")
        results = list_memories(tmp_db, limit=3)
        assert len(results) == 3


class TestCount:
    def test_count_empty(self, tmp_db):
        counts = count_memories(tmp_db)
        assert counts["total"] == 0
        assert counts["by_type"] == {}

    def test_count_by_type(self, tmp_db):
        store_memory(tmp_db, "h1", "tx1", memory_type="transaction")
        store_memory(tmp_db, "h2", "tx2", memory_type="transaction")
        store_memory(tmp_db, "h3", "err1", memory_type="error")

        counts = count_memories(tmp_db)
        assert counts["total"] == 3
        assert counts["by_type"]["transaction"] == 2
        assert counts["by_type"]["error"] == 1


class TestDelete:
    def test_delete_existing(self, tmp_db):
        store_memory(tmp_db, "i1", "to be deleted")
        assert delete_memory(tmp_db, "i1") is True

        counts = count_memories(tmp_db)
        assert counts["total"] == 0

    def test_delete_nonexistent(self, tmp_db):
        assert delete_memory(tmp_db, "nonexistent") is False

    def test_delete_removes_fts(self, tmp_db):
        store_memory(tmp_db, "j1", "searchable content")
        delete_memory(tmp_db, "j1")

        results = query_memories(tmp_db, "searchable")
        assert len(results) == 0
