"""Tests for budget enforcement engine."""

from lightning_memory.lightning import BudgetRule, PreflightDecision, VendorTrust


def test_budget_rule_to_dict():
    rule = BudgetRule(
        vendor="bitrefill.com",
        max_sats_per_txn=1000,
        max_sats_per_day=5000,
    )
    d = rule.to_dict()
    assert d["vendor"] == "bitrefill.com"
    assert d["max_sats_per_txn"] == 1000
    assert d["max_sats_per_day"] == 5000
    assert d["max_sats_per_month"] is None
    assert d["enabled"] is True


def test_preflight_decision_to_dict():
    decision = PreflightDecision(
        verdict="approve",
        vendor="bitrefill.com",
        proposed_sats=500,
    )
    d = decision.to_dict()
    assert d["verdict"] == "approve"
    assert d["reasons"] == []


def test_vendor_trust_to_dict():
    trust = VendorTrust(
        vendor="bitrefill.com",
        kyc_verified=True,
        jurisdiction="US",
        community_score=0.92,
        attestation_count=15,
    )
    d = trust.to_dict()
    assert d["kyc_verified"] is True
    assert d["community_score"] == 0.92


import pytest
from lightning_memory.budget import BudgetEngine
from lightning_memory.db import get_connection, store_memory


@pytest.fixture
def budget_db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def budget_engine(budget_db):
    return BudgetEngine(budget_db)


def test_set_and_get_rule(budget_engine):
    budget_engine.set_rule("bitrefill.com", max_sats_per_txn=1000, max_sats_per_day=5000)
    rule = budget_engine.get_rule("bitrefill.com")
    assert rule is not None
    assert rule.max_sats_per_txn == 1000
    assert rule.max_sats_per_day == 5000


def test_get_rule_nonexistent(budget_engine):
    rule = budget_engine.get_rule("unknown.com")
    assert rule is None


def test_check_txn_limit_ok(budget_engine):
    budget_engine.set_rule("vendor.com", max_sats_per_txn=1000)
    ok, reason = budget_engine.check_limit("vendor.com", 500)
    assert ok is True
    assert reason == ""


def test_check_txn_limit_exceeded(budget_engine):
    budget_engine.set_rule("vendor.com", max_sats_per_txn=1000)
    ok, reason = budget_engine.check_limit("vendor.com", 1500)
    assert ok is False
    assert "per-transaction limit" in reason


def test_check_daily_limit(budget_engine, budget_db):
    budget_engine.set_rule("vendor.com", max_sats_per_day=2000)
    # Simulate prior spending today
    store_memory(budget_db, "tx1", "Paid 800 sats to vendor.com", "transaction",
                 {"vendor": "vendor.com", "amount_sats": 800})
    store_memory(budget_db, "tx2", "Paid 700 sats to vendor.com", "transaction",
                 {"vendor": "vendor.com", "amount_sats": 700})
    # 1500 spent today + proposed 600 = 2100 > 2000
    ok, reason = budget_engine.check_limit("vendor.com", 600)
    assert ok is False
    assert "daily limit" in reason


def test_no_rule_means_approve(budget_engine):
    ok, reason = budget_engine.check_limit("norule.com", 99999)
    assert ok is True


def test_delete_rule(budget_engine):
    budget_engine.set_rule("vendor.com", max_sats_per_txn=100)
    assert budget_engine.get_rule("vendor.com") is not None
    budget_engine.delete_rule("vendor.com")
    assert budget_engine.get_rule("vendor.com") is None


def test_list_rules(budget_engine):
    budget_engine.set_rule("a.com", max_sats_per_txn=100)
    budget_engine.set_rule("b.com", max_sats_per_txn=200)
    rules = budget_engine.list_rules()
    assert len(rules) == 2
