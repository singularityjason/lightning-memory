"""Lightning intelligence data models.

Structured representations derived from raw memory content.
Used by the intelligence engine to return typed, actionable data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LightningPayment:
    """A single Lightning payment extracted from memory."""

    amount_sats: int
    destination: str
    fee_sats: int = 0
    status: str = "completed"  # completed | failed | pending
    protocol: str = "lightning"  # lightning | l402 | keysend


@dataclass
class VendorReputation:
    """Aggregated reputation for a vendor based on transaction memories."""

    vendor: str
    total_txns: int = 0
    total_sats: int = 0
    success_rate: float = 1.0  # 0.0 to 1.0
    avg_sats: float = 0.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "total_txns": self.total_txns,
            "total_sats": self.total_sats,
            "success_rate": self.success_rate,
            "avg_sats": self.avg_sats,
            "tags": self.tags,
        }


@dataclass
class SpendingSummary:
    """Structured breakdown of agent spending over a period."""

    total_sats: int = 0
    by_vendor: dict[str, int] = field(default_factory=dict)
    by_protocol: dict[str, int] = field(default_factory=dict)
    period: str = "30d"
    txn_count: int = 0

    def to_dict(self) -> dict:
        return {
            "total_sats": self.total_sats,
            "by_vendor": self.by_vendor,
            "by_protocol": self.by_protocol,
            "period": self.period,
            "txn_count": self.txn_count,
        }


@dataclass
class AnomalyReport:
    """Result of checking a proposed payment against historical patterns."""

    verdict: str = "normal"  # normal | high | first_time
    context: str = ""
    avg_historical_sats: float = 0.0
    proposed_sats: int = 0
    vendor: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "context": self.context,
            "avg_historical_sats": self.avg_historical_sats,
            "proposed_sats": self.proposed_sats,
            "vendor": self.vendor,
        }


@dataclass
class BudgetRule:
    """Spending limit rule for a vendor."""

    vendor: str
    max_sats_per_txn: int | None = None
    max_sats_per_day: int | None = None
    max_sats_per_month: int | None = None
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "max_sats_per_txn": self.max_sats_per_txn,
            "max_sats_per_day": self.max_sats_per_day,
            "max_sats_per_month": self.max_sats_per_month,
            "enabled": self.enabled,
        }


@dataclass
class VendorTrust:
    """Combined trust profile for a vendor."""

    vendor: str
    kyc_verified: bool = False
    jurisdiction: str = ""
    community_score: float = 0.0
    attestation_count: int = 0
    local_reputation: VendorReputation | None = None

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "kyc_verified": self.kyc_verified,
            "jurisdiction": self.jurisdiction,
            "community_score": self.community_score,
            "attestation_count": self.attestation_count,
            "local_reputation": self.local_reputation.to_dict() if self.local_reputation else None,
        }


@dataclass
class PreflightDecision:
    """Result of a payment pre-flight check."""

    verdict: str = "approve"  # approve | reject | escalate
    vendor: str = ""
    proposed_sats: int = 0
    reasons: list[str] = field(default_factory=list)
    budget_remaining_today: int | None = None
    anomaly_verdict: str = ""
    trust_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "vendor": self.vendor,
            "proposed_sats": self.proposed_sats,
            "reasons": self.reasons,
            "budget_remaining_today": self.budget_remaining_today,
            "anomaly_verdict": self.anomaly_verdict,
            "trust_score": self.trust_score,
        }
