"""Tests for vendor name normalization across all subsystems."""

import json
import sqlite3

import pytest

from lightning_memory.memory import MemoryEngine, normalize_vendor
from lightning_memory.intelligence import IntelligenceEngine
from lightning_memory.budget import BudgetEngine
from lightning_memory.trust import TrustEngine
from lightning_memory.db import get_connection


# --- normalize_vendor unit tests ---


def test_normalize_lowercase():
    assert normalize_vendor("BitRefill.com") == "bitrefill.com"
    assert normalize_vendor("OPENAI") == "openai"


def test_normalize_strip_www():
    assert normalize_vendor("www.bitrefill.com") == "bitrefill.com"
    assert normalize_vendor("WWW.BITREFILL.COM") == "bitrefill.com"


def test_normalize_strip_protocol():
    assert normalize_vendor("https://bitrefill.com") == "bitrefill.com"
    assert normalize_vendor("http://bitrefill.com") == "bitrefill.com"
    assert normalize_vendor("https://www.bitrefill.com") == "bitrefill.com"


def test_normalize_strip_trailing_slash():
    assert normalize_vendor("bitrefill.com/") == "bitrefill.com"
    assert normalize_vendor("https://www.bitrefill.com/") == "bitrefill.com"


def test_normalize_strip_whitespace():
    assert normalize_vendor("  bitrefill.com  ") == "bitrefill.com"


def test_normalize_already_clean():
    assert normalize_vendor("bitrefill.com") == "bitrefill.com"
    assert normalize_vendor("openai") == "openai"


def test_normalize_subdomain_api():
    assert normalize_vendor("api.openai.com") == "openai.com"
    assert normalize_vendor("https://api.openai.com/v1") == "openai.com"


def test_normalize_subdomain_app():
    assert normalize_vendor("app.example.io") == "example.io"


def test_normalize_subdomain_gateway():
    assert normalize_vendor("gateway.bitrefill.com") == "bitrefill.com"
    assert normalize_vendor("gw.bitrefill.com") == "bitrefill.com"


def test_normalize_strips_path():
    assert normalize_vendor("bitrefill.com/api/v1") == "bitrefill.com"
    assert normalize_vendor("https://api.openai.com/v1/chat") == "openai.com"


def test_normalize_empty():
    assert normalize_vendor("") == ""


# --- Cross-subsystem integration tests ---


@pytest.fixture
def conn():
    return get_connection(":memory:")


@pytest.fixture
def engine(conn):
    from lightning_memory.nostr import NostrIdentity
    identity = NostrIdentity.generate()
    return MemoryEngine(conn=conn, identity=identity)


def _store_txn(engine, vendor, amount, content=None):
    """Helper to store a transaction memory."""
    if content is None:
        content = f"Paid {amount} sats to {vendor}"
    engine.store(
        content=content,
        memory_type="transaction",
        metadata={"vendor": vendor, "amount_sats": amount},
    )


def test_intelligence_cross_variant_reputation(conn, engine):
    """Vendor reputation should aggregate across name variants."""
    _store_txn(engine, "bitrefill.com", 500)
    _store_txn(engine, "www.bitrefill.com", 300)
    _store_txn(engine, "https://Bitrefill.com/", 200)

    intel = IntelligenceEngine(conn=conn)
    rep = intel.vendor_report("bitrefill.com")

    assert rep.total_txns == 3
    assert rep.total_sats == 1000


def test_budget_cross_variant_enforcement(conn, engine):
    """Budget rules should apply regardless of vendor name variant."""
    budget = BudgetEngine(conn=conn)
    budget.set_rule("bitrefill.com", max_sats_per_txn=1000)

    # Rule should be found with different variants
    assert budget.get_rule("www.bitrefill.com") is not None
    assert budget.get_rule("https://BITREFILL.COM/") is not None

    # Check limit should work with variant
    ok, _ = budget.check_limit("WWW.bitrefill.com", 500)
    assert ok


def test_budget_spent_today_cross_variant(conn, engine):
    """Daily spending should aggregate across vendor name variants."""
    _store_txn(engine, "bitrefill.com", 500)
    _store_txn(engine, "www.bitrefill.com", 300)

    budget = BudgetEngine(conn=conn)
    spent = budget.spent_today("https://bitrefill.com")
    assert spent == 800


def test_trust_cross_variant_kyc(conn):
    """KYC status should match across vendor name variants."""
    trust = TrustEngine(conn=conn)
    trust.set_vendor_kyc("www.bitrefill.com", verified=True, jurisdiction="EU")

    kyc = trust.get_vendor_kyc("bitrefill.com")
    assert kyc["kyc_verified"] is True

    kyc2 = trust.get_vendor_kyc("https://BITREFILL.COM/")
    assert kyc2["kyc_verified"] is True


def test_trust_cross_variant_community_reputation(conn, engine):
    """Community attestations should aggregate across vendor variants."""
    from lightning_memory.db import store_memory

    store_memory(conn, "att1", "Trust attestation for bitrefill", "attestation",
                 {"vendor": "www.bitrefill.com", "trust_score": 0.9})
    store_memory(conn, "att2", "Trust attestation for bitrefill", "attestation",
                 {"vendor": "bitrefill.com", "trust_score": 0.8})

    trust = TrustEngine(conn=conn)
    score, count = trust.community_reputation("https://BITREFILL.COM")

    assert count == 2
    assert abs(score - 0.85) < 0.01
