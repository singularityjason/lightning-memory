"""Lightning intelligence engine.

Derives vendor reputations, spending summaries, and anomaly detection
from existing transaction memories. No new schema needed — reads from
the memories table and aggregates.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .lightning import AnomalyReport, SpendingSummary, VendorReputation


class IntelligenceEngine:
    """Aggregation and analysis layer over stored memories."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def vendor_report(self, vendor: str) -> VendorReputation:
        """Build a reputation report for a vendor from transaction memories."""
        rows = self.conn.execute(
            """SELECT content, metadata FROM memories
               WHERE type = 'transaction'
               ORDER BY created_at DESC""",
        ).fetchall()

        vendor_lower = vendor.lower()
        rep = VendorReputation(vendor=vendor)
        tags: set[str] = set()

        for row in rows:
            content = row["content"].lower()
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            meta_vendor = meta.get("vendor", "").lower()

            # Match vendor in metadata or content
            if vendor_lower not in meta_vendor and vendor_lower not in content:
                continue

            rep.total_txns += 1
            amount = _extract_amount(meta, content)
            rep.total_sats += amount

            # Check for failure indicators
            if any(w in content for w in ("fail", "error", "timeout", "reject")):
                tags.add("has_failures")

            protocol = meta.get("protocol", "")
            if protocol:
                tags.add(protocol)

        if rep.total_txns > 0:
            rep.avg_sats = rep.total_sats / rep.total_txns
            # Count failures from content analysis
            failure_count = self._count_vendor_failures(vendor_lower)
            rep.success_rate = max(0.0, 1.0 - (failure_count / rep.total_txns))

        rep.tags = sorted(tags)
        return rep

    def spending_summary(self, since: str = "30d") -> SpendingSummary:
        """Aggregate spending across all transaction memories."""
        from .memory import MemoryEngine

        since_ts = MemoryEngine._parse_since(None, since)  # type: ignore[arg-type]

        rows = self.conn.execute(
            """SELECT content, metadata FROM memories
               WHERE type = 'transaction' AND created_at >= ?
               ORDER BY created_at DESC""",
            (since_ts,),
        ).fetchall()

        summary = SpendingSummary(period=since)

        for row in rows:
            content = row["content"]
            meta = json.loads(row["metadata"]) if row["metadata"] else {}

            amount = _extract_amount(meta, content)
            vendor = meta.get("vendor", "unknown")
            protocol = meta.get("protocol", "lightning")

            summary.total_sats += amount
            summary.txn_count += 1
            summary.by_vendor[vendor] = summary.by_vendor.get(vendor, 0) + amount
            summary.by_protocol[protocol] = summary.by_protocol.get(protocol, 0) + amount

        return summary

    def anomaly_check(self, vendor: str, amount_sats: int) -> AnomalyReport:
        """Check if a proposed payment looks normal compared to history."""
        report = AnomalyReport(vendor=vendor, proposed_sats=amount_sats)
        rep = self.vendor_report(vendor)

        if rep.total_txns == 0:
            report.verdict = "first_time"
            report.context = f"No prior transactions with {vendor}."
            return report

        report.avg_historical_sats = rep.avg_sats

        if rep.avg_sats > 0 and amount_sats > rep.avg_sats * 3:
            report.verdict = "high"
            report.context = (
                f"Proposed {amount_sats} sats is {amount_sats / rep.avg_sats:.1f}x "
                f"the historical average of {rep.avg_sats:.0f} sats "
                f"across {rep.total_txns} transactions."
            )
        else:
            report.verdict = "normal"
            report.context = (
                f"Proposed {amount_sats} sats is within normal range. "
                f"Historical average: {rep.avg_sats:.0f} sats "
                f"across {rep.total_txns} transactions."
            )

        return report

    def _count_vendor_failures(self, vendor_lower: str) -> int:
        """Count error/failure memories mentioning this vendor."""
        rows = self.conn.execute(
            """SELECT content, metadata FROM memories
               WHERE type IN ('transaction', 'error')""",
        ).fetchall()

        count = 0
        for row in rows:
            content = row["content"].lower()
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            meta_vendor = meta.get("vendor", "").lower()

            if vendor_lower not in meta_vendor and vendor_lower not in content:
                continue

            if any(w in content for w in ("fail", "error", "timeout", "reject")):
                count += 1

        return count


def _extract_amount(meta: dict[str, Any], content: str) -> int:
    """Extract sats amount from metadata or content text."""
    # Prefer structured metadata
    if "amount_sats" in meta:
        try:
            return int(meta["amount_sats"])
        except (ValueError, TypeError):
            pass

    # Fallback: parse from content (e.g., "500 sats", "paid 1000 sats")
    import re

    match = re.search(r"(\d+)\s*sats?", content.lower())
    if match:
        return int(match.group(1))

    return 0
