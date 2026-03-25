"""Tests for memory quality: contradictions, access tracking, UTC dates."""

import re
import time

import pytest

from lightning_memory.db import get_connection, format_utc
from lightning_memory.memory import MemoryEngine
from lightning_memory.nostr import NostrIdentity


@pytest.fixture
def engine():
    conn = get_connection(":memory:")
    identity = NostrIdentity.generate()
    return MemoryEngine(conn=conn, identity=identity)


# --- UTC date format tests ---


def test_format_utc_produces_iso8601():
    result = format_utc(1711324800.0)
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result)


def test_format_utc_none():
    assert format_utc(None) is None


def test_store_returns_utc_created_at(engine):
    result = engine.store("Test memory", memory_type="general")
    assert result["created_at"].endswith("Z")
    assert "T" in result["created_at"]


def test_query_returns_utc_created_at(engine):
    engine.store("Paid 500 sats to bitrefill for gift card", memory_type="transaction")
    results = engine.query("bitrefill")
    assert len(results) >= 1
    assert results[0]["created_at"].endswith("Z")


def test_list_returns_utc_created_at(engine):
    engine.store("Test memory for listing", memory_type="general")
    results = engine.list()
    assert len(results) >= 1
    assert results[0]["created_at"].endswith("Z")


def test_edit_returns_utc_timestamps(engine):
    r = engine.store("Original content here for editing", memory_type="vendor")
    edited = engine.edit(r["id"], new_content="Updated content here for editing")
    assert edited["created_at"].endswith("Z")
    assert edited["updated_at"].endswith("Z")


# --- Access tracking tests ---


def test_query_increments_access_count(engine):
    engine.store("Paid 500 sats to bitrefill for card", memory_type="transaction")
    engine.query("bitrefill")
    engine.query("bitrefill")
    engine.query("bitrefill")

    row = engine.conn.execute(
        "SELECT access_count, last_accessed_at FROM memories LIMIT 1"
    ).fetchone()
    assert row["access_count"] == 3
    assert row["last_accessed_at"] is not None
    assert row["last_accessed_at"] > 0


def test_list_does_not_increment_access(engine):
    engine.store("Something for listing only", memory_type="general")
    engine.list()
    engine.list()

    row = engine.conn.execute(
        "SELECT access_count FROM memories LIMIT 1"
    ).fetchone()
    assert row["access_count"] == 0  # list doesn't bump access


def test_unqueried_memories_have_zero_access(engine):
    engine.store("Never queried memory content", memory_type="general")
    row = engine.conn.execute(
        "SELECT access_count, last_accessed_at FROM memories LIMIT 1"
    ).fetchone()
    assert row["access_count"] == 0
    assert row["last_accessed_at"] is None


# --- Contradiction detection tests ---


def test_price_contradiction_detected(engine):
    """Storing a transaction with 3x+ price change should flag contradiction."""
    engine.store(
        "Paid 100 sats to bitrefill for card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 100},
    )
    r2 = engine.store(
        "Paid 500 sats to bitrefill for card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 500},
    )
    assert "contradictions" in r2
    assert len(r2["contradictions"]) >= 1
    assert r2["contradictions"][0]["type"] == "price_change"
    assert "5.0x" in r2["contradictions"][0]["detail"]


def test_sentiment_contradiction_detected(engine):
    """Positive then negative about same vendor should flag contradiction."""
    engine.store(
        "Bitrefill is reliable and fast, always delivers",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    r2 = engine.store(
        "Bitrefill is unreliable, avoid this vendor",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    assert "contradictions" in r2
    assert any(c["type"] == "sentiment_conflict" for c in r2["contradictions"])


def test_no_contradiction_for_same_sentiment(engine):
    """Two positive memories about same vendor should not flag contradiction."""
    engine.store(
        "Bitrefill is reliable and fast",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    r2 = engine.store(
        "Bitrefill delivered quickly, great service",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    assert "contradictions" not in r2 or len(r2.get("contradictions", [])) == 0


def test_no_contradiction_without_vendor(engine):
    """Memories without vendor metadata should not trigger contradiction checks."""
    engine.store("General memory about nothing", memory_type="general")
    r2 = engine.store("Another general memory about stuff", memory_type="general")
    assert "contradictions" not in r2 or len(r2.get("contradictions", [])) == 0


def test_no_price_contradiction_for_small_changes(engine):
    """Price changes under 3x should not be flagged."""
    engine.store(
        "Paid 100 sats to openai for query",
        memory_type="transaction",
        metadata={"vendor": "openai.com", "amount_sats": 100},
    )
    r2 = engine.store(
        "Paid 150 sats to openai for query",
        memory_type="transaction",
        metadata={"vendor": "openai.com", "amount_sats": 150},
    )
    assert "contradictions" not in r2 or len(r2.get("contradictions", [])) == 0


def test_contradiction_includes_existing_preview(engine):
    """Contradiction should include a preview of the conflicting memory."""
    engine.store(
        "Bitrefill is the best vendor for gift cards, highly recommended",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    r2 = engine.store(
        "Bitrefill is terrible, scam vendor avoid at all costs",
        memory_type="vendor",
        metadata={"vendor": "bitrefill.com"},
    )
    assert r2["contradictions"][0]["existing_preview"]
    assert r2["contradictions"][0]["existing_created_at"].endswith("Z")
