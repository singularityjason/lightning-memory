"""Compliance report generation for regulatory compliance stacks."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .memory import parse_since
from .nostr import NostrIdentity


class ComplianceEngine:
    """Generates structured compliance reports from local data."""

    def __init__(self, conn: sqlite3.Connection, identity: NostrIdentity):
        self.conn = conn
        self.identity = identity

    def generate_report(self, since: str = "30d") -> dict[str, Any]:
        """Generate a compliance report.

        The `since` parameter scopes temporal data (transactions, attestations).
        Current-state data (budget rules, vendor KYC, agent attestations) is
        always included regardless of time period.
        """
        since_ts = parse_since(since)

        return {
            "generated_at": time.time(),
            "period_since": since_ts,
            "agent_identity": self._agent_identity(),
            "transactions": self._transactions(since_ts),
            "budget_rules": self._budget_rules(),
            "vendor_kyc": self._vendor_kyc(),
            "anomaly_flags": self._anomaly_flags(since_ts),
            "trust_attestations": self._trust_attestations(since_ts),
        }

    def _agent_identity(self) -> dict[str, Any]:
        """Agent pubkey and any self-attestation."""
        result: dict[str, Any] = {"pubkey": self.identity.public_key_hex}
        row = self.conn.execute(
            "SELECT * FROM agent_attestations WHERE agent_pubkey = ?",
            (self.identity.public_key_hex,),
        ).fetchone()
        if row:
            result["owner_id"] = row["owner_id"]
            result["jurisdiction"] = row["jurisdiction"]
            result["compliance_level"] = row["compliance_level"]
            result["verification_source"] = row["verification_source"]
        return result

    def _transactions(self, since_ts: float) -> list[dict]:
        """Transaction memories in time period."""
        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'transaction' AND created_at >= ? ORDER BY created_at DESC",
            (since_ts,),
        ).fetchall()
        results = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            results.append({
                "content": row["content"],
                "vendor": meta.get("vendor", ""),
                "amount_sats": meta.get("amount_sats", 0),
                "timestamp": row["created_at"],
            })
        return results

    def _budget_rules(self) -> list[dict]:
        """All active budget rules (current state)."""
        rows = self.conn.execute(
            "SELECT * FROM budget_rules WHERE enabled = 1"
        ).fetchall()
        return [
            {
                "vendor": row["vendor"],
                "max_sats_per_txn": row["max_sats_per_txn"],
                "max_sats_per_day": row["max_sats_per_day"],
                "max_sats_per_month": row["max_sats_per_month"],
            }
            for row in rows
        ]

    def _vendor_kyc(self) -> list[dict]:
        """KYC status for all known vendors (current state)."""
        rows = self.conn.execute("SELECT * FROM vendor_kyc").fetchall()
        return [
            {
                "vendor": row["vendor"],
                "kyc_verified": bool(row["kyc_verified"]),
                "jurisdiction": row["jurisdiction"],
                "verification_source": row["verification_source"],
            }
            for row in rows
        ]

    def _anomaly_flags(self, since_ts: float) -> list[dict]:
        """Transactions in period that would trigger anomaly detection."""
        from .intelligence import IntelligenceEngine
        intel = IntelligenceEngine(conn=self.conn)

        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'transaction' AND created_at >= ?",
            (since_ts,),
        ).fetchall()
        flags = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            vendor = meta.get("vendor", "")
            amount = meta.get("amount_sats", 0)
            if vendor and amount:
                report = intel.anomaly_check(vendor, amount)
                if report.verdict != "normal":
                    flags.append({
                        "vendor": vendor,
                        "amount_sats": amount,
                        "verdict": report.verdict,
                        "timestamp": row["created_at"],
                    })
        return flags

    def _trust_attestations(self, since_ts: float) -> list[dict]:
        """Attestation memories in time period."""
        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'attestation' AND created_at >= ? ORDER BY created_at DESC",
            (since_ts,),
        ).fetchall()
        results = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            results.append({
                "vendor": meta.get("vendor", ""),
                "trust_score": meta.get("trust_score", 0),
                "attester": meta.get("attester", ""),
                "timestamp": row["created_at"],
            })
        return results
