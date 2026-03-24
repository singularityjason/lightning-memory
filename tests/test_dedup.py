"""Tests for memory deduplication."""

import pytest

from lightning_memory.db import get_connection
from lightning_memory.memory import MemoryEngine, _jaccard
from lightning_memory.nostr import NostrIdentity


@pytest.fixture
def engine():
    conn = get_connection(":memory:")
    identity = NostrIdentity.generate()
    return MemoryEngine(conn=conn, identity=identity)


# --- Jaccard unit tests ---


def test_jaccard_identical():
    assert _jaccard("Paid 500 sats to bitrefill", "Paid 500 sats to bitrefill") == 1.0


def test_jaccard_empty():
    assert _jaccard("", "something") == 0.0
    assert _jaccard("hi", "") == 0.0  # words too short (< 4 chars)


def test_jaccard_no_overlap():
    assert _jaccard("Paid vendor lightning sats", "bought coffee morning shop") == 0.0


def test_jaccard_partial_overlap():
    score = _jaccard(
        "Paid 500 sats to bitrefill for gift card",
        "Paid 500 sats to bitrefill for Amazon gift card",
    )
    assert score > 0.7  # high overlap


def test_jaccard_short_words_ignored():
    """Words shorter than 3 chars should be ignored."""
    score = _jaccard("a b c d e f g", "a b c d e f g")
    assert score == 0.0  # all words too short (< 3 chars)


# --- Dedup integration tests ---


def test_exact_duplicate_detected(engine):
    """Storing the same content twice should return dedup flag."""
    r1 = engine.store("Paid 500 sats to bitrefill for a gift card", memory_type="transaction")
    r2 = engine.store("Paid 500 sats to bitrefill for a gift card", memory_type="transaction")

    assert "dedup" not in r1
    assert r2.get("dedup") is True
    assert r2["id"] == r1["id"]


def test_near_duplicate_detected(engine):
    """Storing near-identical content should be deduped."""
    r1 = engine.store(
        "Paid 500 sats to bitrefill for a $5 Amazon gift card via L402",
        memory_type="vendor",  # vendor type has 0.80 threshold
    )
    r2 = engine.store(
        "Paid 500 sats to bitrefill for a $5 Amazon gift card through L402",
        memory_type="vendor",
    )

    assert r2.get("dedup") is True
    assert r2["id"] == r1["id"]


def test_transaction_dedup_with_same_metadata(engine):
    """Transaction dedup should work when vendor + amount match."""
    r1 = engine.store(
        "Paid 500 sats to bitrefill for a gift card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 500},
    )
    r2 = engine.store(
        "Paid 500 sats to bitrefill for a gift card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 500},
    )

    assert r2.get("dedup") is True
    assert r2["id"] == r1["id"]


def test_transaction_different_amount_not_deduped(engine):
    """Transactions with different amounts should not be deduped."""
    r1 = engine.store(
        "Paid 500 sats to bitrefill for card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 500},
    )
    r2 = engine.store(
        "Paid 300 sats to bitrefill for card",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 300},
    )

    assert "dedup" not in r2
    assert r2["id"] != r1["id"]


def test_different_content_not_deduped(engine):
    """Genuinely different content should not be deduped."""
    r1 = engine.store(
        "Paid 500 sats to bitrefill for a gift card",
        memory_type="transaction",
    )
    r2 = engine.store(
        "Paid 1000 sats to openai for API access via L402 endpoint",
        memory_type="transaction",
    )

    assert "dedup" not in r2
    assert r2["id"] != r1["id"]


def test_different_types_not_deduped(engine):
    """Same content but different types should not be deduped."""
    r1 = engine.store("Important vendor bitrefill is reliable", memory_type="vendor")
    r2 = engine.store("Important vendor bitrefill is reliable", memory_type="decision")

    assert "dedup" not in r2
    assert r2["id"] != r1["id"]


def test_dedup_returns_existing_metadata(engine):
    """Dedup result should include the original memory's metadata."""
    r1 = engine.store(
        "Paid 500 sats to bitrefill",
        memory_type="transaction",
        metadata={"vendor": "bitrefill.com", "amount_sats": 500},
    )
    r2 = engine.store(
        "Paid 500 sats to bitrefill",
        memory_type="transaction",
    )

    assert r2.get("dedup") is True
    assert r2["metadata"]["vendor"] == "bitrefill.com"


def test_memory_count_unchanged_on_dedup(engine):
    """Dedup should not create a new memory record."""
    engine.store("Paid 500 sats to bitrefill for card", memory_type="transaction")
    engine.store("Paid 500 sats to bitrefill for card", memory_type="transaction")
    engine.store("Paid 500 sats to bitrefill for card", memory_type="transaction")

    stats = engine.stats()
    assert stats["total"] == 1
