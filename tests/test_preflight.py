"""Tests for payment pre-flight gate."""

import pytest
from lightning_memory.preflight import PreflightEngine
from lightning_memory.budget import BudgetEngine
from lightning_memory.trust import TrustEngine
from lightning_memory.db import get_connection, store_memory


@pytest.fixture
def pf_db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def pf_engine(pf_db):
    return PreflightEngine(pf_db)


def test_approve_no_rules(pf_engine):
    """No budget rules + no history = approve."""
    decision = pf_engine.check("newvendor.com", 5)
    assert decision.verdict == "approve"


def test_reject_over_budget(pf_engine, pf_db):
    """Exceeding per-transaction budget = reject."""
    budget = BudgetEngine(pf_db)
    budget.set_rule("vendor.com", max_sats_per_txn=500)
    decision = pf_engine.check("vendor.com", 1000)
    assert decision.verdict == "reject"
    assert any("per-transaction limit" in r for r in decision.reasons)


def test_escalate_anomaly(pf_engine, pf_db):
    """High anomaly with no budget rule = escalate."""
    for i in range(5):
        store_memory(pf_db, f"tx{i}", f"Paid 50 sats to vendor.com",
                     "transaction", {"vendor": "vendor.com", "amount_sats": 50})
    decision = pf_engine.check("vendor.com", 5000)
    assert decision.verdict == "escalate"
    assert decision.anomaly_verdict == "high"


def test_approve_normal_payment(pf_engine, pf_db):
    """Normal payment within budget = approve."""
    budget = BudgetEngine(pf_db)
    budget.set_rule("vendor.com", max_sats_per_txn=1000)
    for i in range(3):
        store_memory(pf_db, f"tx{i}", f"Paid 100 sats to vendor.com",
                     "transaction", {"vendor": "vendor.com", "amount_sats": 100})
    decision = pf_engine.check("vendor.com", 120)
    assert decision.verdict == "approve"


def test_escalate_first_time_vendor(pf_engine):
    """First-time vendor with significant amount = escalate."""
    decision = pf_engine.check("neverpaid.com", 500)
    assert decision.verdict == "escalate"
    assert decision.anomaly_verdict == "first_time"


def test_approve_first_time_small_amount(pf_engine):
    """First-time vendor with tiny amount = approve."""
    decision = pf_engine.check("neverpaid.com", 5)
    assert decision.verdict == "approve"


def test_kyc_trusted_downgrades_escalate(pf_engine, pf_db):
    """KYC-verified vendor with high community score downgrades escalate to approve."""
    trust = TrustEngine(pf_db)
    trust.set_vendor_kyc("vendor.com", verified=True, jurisdiction="EU")
    # Add community attestations
    store_memory(pf_db, "att1", "Trust attestation", "attestation",
                 {"vendor": "vendor.com", "trust_score": 0.9})
    store_memory(pf_db, "att2", "Trust attestation", "attestation",
                 {"vendor": "vendor.com", "trust_score": 0.85})
    # This would normally escalate as first_time (no transactions)
    decision = pf_engine.check("vendor.com", 500)
    assert decision.verdict == "approve"
    assert any("Downgraded" in r for r in decision.reasons)
