"""Payment pre-flight gate.

Combines budget limits, anomaly detection, and vendor trust
into a single approve/reject/escalate decision before payment.
"""

from __future__ import annotations

import sqlite3

from .budget import BudgetEngine
from .intelligence import IntelligenceEngine
from .lightning import PreflightDecision
from .trust import TrustEngine

# Payments under this threshold skip escalation for first-time vendors
SMALL_PAYMENT_THRESHOLD = 10  # sats


class PreflightEngine:
    """Pre-flight check combining budget + anomaly + trust."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.budget = BudgetEngine(conn)
        self.intel = IntelligenceEngine(conn)
        self.trust = TrustEngine(conn)

    def check(self, vendor: str, amount_sats: int) -> PreflightDecision:
        """Run all pre-flight checks and return a decision."""
        decision = PreflightDecision(vendor=vendor, proposed_sats=amount_sats)
        reasons: list[str] = []

        # 1. Budget check (hard limit — reject if exceeded)
        budget_ok, budget_reason = self.budget.check_limit(vendor, amount_sats)
        if not budget_ok:
            decision.verdict = "reject"
            reasons.append(budget_reason)
            decision.reasons = reasons
            return decision

        # Budget remaining info
        rule = self.budget.get_rule(vendor)
        if rule and rule.max_sats_per_day:
            spent = self.budget.spent_today(vendor)
            decision.budget_remaining_today = rule.max_sats_per_day - spent

        # 2. Anomaly check (soft signal — escalate if high)
        anomaly = self.intel.anomaly_check(vendor, amount_sats)
        decision.anomaly_verdict = anomaly.verdict

        if anomaly.verdict == "high":
            decision.verdict = "escalate"
            reasons.append(anomaly.context)

        if anomaly.verdict == "first_time" and amount_sats > SMALL_PAYMENT_THRESHOLD:
            decision.verdict = "escalate"
            reasons.append(f"First-time vendor: {vendor}. Consider starting with a smaller payment.")

        # 3. Trust score (informational, used for escalate/approve edge cases)
        profile = self.trust.vendor_trust_profile(vendor)
        decision.trust_score = profile.community_score

        # If KYC verified and community trusted, downgrade escalate to approve
        if decision.verdict == "escalate" and profile.kyc_verified and profile.community_score >= 0.8:
            decision.verdict = "approve"
            reasons.append(f"Downgraded from escalate: vendor is KYC-verified with community score {profile.community_score:.2f}")

        decision.reasons = reasons
        if not reasons:
            decision.verdict = "approve"
        return decision
