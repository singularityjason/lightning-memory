"""Tests for vendor trust and community reputation."""

import pytest
from lightning_memory.trust import TrustEngine
from lightning_memory.db import get_connection, store_memory


@pytest.fixture
def trust_db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def trust_engine(trust_db):
    return TrustEngine(trust_db)


def test_set_vendor_kyc(trust_engine):
    trust_engine.set_vendor_kyc("bitrefill.com", verified=True, jurisdiction="EU")
    status = trust_engine.get_vendor_kyc("bitrefill.com")
    assert status["kyc_verified"] is True
    assert status["jurisdiction"] == "EU"


def test_unverified_vendor(trust_engine):
    status = trust_engine.get_vendor_kyc("unknown.com")
    assert status["kyc_verified"] is False


def test_vendor_trust_profile(trust_engine, trust_db):
    """Full trust profile combines KYC + local reputation."""
    store_memory(trust_db, "tx1", "Paid 100 sats to vendor.com", "transaction",
                 {"vendor": "vendor.com", "amount_sats": 100})
    store_memory(trust_db, "tx2", "Paid 200 sats to vendor.com", "transaction",
                 {"vendor": "vendor.com", "amount_sats": 200})
    trust_engine.set_vendor_kyc("vendor.com", verified=True, jurisdiction="US")

    profile = trust_engine.vendor_trust_profile("vendor.com")
    assert profile.kyc_verified is True
    assert profile.local_reputation is not None
    assert profile.local_reputation.total_txns == 2


def test_community_score_no_attestations(trust_engine):
    """Community score is 0 when no attestations exist."""
    profile = trust_engine.vendor_trust_profile("new.com")
    assert profile.community_score == 0.0
    assert profile.attestation_count == 0


def test_community_score_with_attestations(trust_engine, trust_db):
    """Community score averages trust_score from attestation memories."""
    import json
    store_memory(trust_db, "att1", "Trust attestation for vendor.com",
                 "attestation", {"vendor": "vendor.com", "trust_score": 0.9})
    store_memory(trust_db, "att2", "Trust attestation for vendor.com",
                 "attestation", {"vendor": "vendor.com", "trust_score": 0.8})
    score, count = trust_engine.community_reputation("vendor.com")
    assert count == 2
    assert abs(score - 0.85) < 0.01
