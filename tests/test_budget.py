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
