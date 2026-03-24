"""Budget enforcement engine.

Stores per-vendor spending limits and checks proposed payments
against those limits using transaction history from the memories table.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from .lightning import BudgetRule
from .memory import normalize_vendor


class BudgetEngine:
    """CRUD and enforcement for vendor spending limits."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set_rule(
        self,
        vendor: str,
        max_sats_per_txn: int | None = None,
        max_sats_per_day: int | None = None,
        max_sats_per_month: int | None = None,
    ) -> BudgetRule:
        """Create or update a budget rule for a vendor."""
        vendor = normalize_vendor(vendor)
        now = time.time()
        # Check for existing rule for this vendor
        existing = self.conn.execute(
            "SELECT id FROM budget_rules WHERE vendor = ?", (vendor,)
        ).fetchone()
        rule_id = existing["id"] if existing else str(uuid.uuid4())[:8]

        self.conn.execute(
            """INSERT INTO budget_rules (id, vendor, max_sats_per_txn, max_sats_per_day,
                   max_sats_per_month, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   max_sats_per_txn=excluded.max_sats_per_txn,
                   max_sats_per_day=excluded.max_sats_per_day,
                   max_sats_per_month=excluded.max_sats_per_month,
                   updated_at=excluded.updated_at""",
            (rule_id, vendor, max_sats_per_txn, max_sats_per_day,
             max_sats_per_month, now, now),
        )
        self.conn.commit()
        return BudgetRule(
            vendor=vendor,
            max_sats_per_txn=max_sats_per_txn,
            max_sats_per_day=max_sats_per_day,
            max_sats_per_month=max_sats_per_month,
        )

    def get_rule(self, vendor: str) -> BudgetRule | None:
        """Get the budget rule for a vendor, or None."""
        vendor = normalize_vendor(vendor)
        row = self.conn.execute(
            "SELECT * FROM budget_rules WHERE vendor = ? AND enabled = 1",
            (vendor,),
        ).fetchone()
        if not row:
            return None
        return BudgetRule(
            vendor=row["vendor"],
            max_sats_per_txn=row["max_sats_per_txn"],
            max_sats_per_day=row["max_sats_per_day"],
            max_sats_per_month=row["max_sats_per_month"],
            enabled=bool(row["enabled"]),
        )

    def delete_rule(self, vendor: str) -> bool:
        """Delete a budget rule. Returns True if deleted."""
        vendor = normalize_vendor(vendor)
        cursor = self.conn.execute(
            "DELETE FROM budget_rules WHERE vendor = ?", (vendor,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_rules(self) -> list[BudgetRule]:
        """List all active budget rules."""
        rows = self.conn.execute(
            "SELECT * FROM budget_rules WHERE enabled = 1 ORDER BY vendor"
        ).fetchall()
        return [
            BudgetRule(
                vendor=r["vendor"],
                max_sats_per_txn=r["max_sats_per_txn"],
                max_sats_per_day=r["max_sats_per_day"],
                max_sats_per_month=r["max_sats_per_month"],
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    def check_limit(self, vendor: str, proposed_sats: int) -> tuple[bool, str]:
        """Check if a proposed payment is within budget limits.

        Returns (ok, reason). If ok is True, reason is empty.
        """
        vendor = normalize_vendor(vendor)
        rule = self.get_rule(vendor)
        if rule is None:
            return True, ""

        # Per-transaction check
        if rule.max_sats_per_txn is not None and proposed_sats > rule.max_sats_per_txn:
            return False, (
                f"{proposed_sats} sats exceeds per-transaction limit "
                f"of {rule.max_sats_per_txn} sats for {vendor}"
            )

        # Daily spending check
        if rule.max_sats_per_day is not None:
            spent_today = self._spent_since(vendor, _start_of_day())
            if spent_today + proposed_sats > rule.max_sats_per_day:
                return False, (
                    f"{proposed_sats} sats would exceed daily limit of "
                    f"{rule.max_sats_per_day} sats for {vendor} "
                    f"({spent_today} already spent today)"
                )

        # Monthly spending check
        if rule.max_sats_per_month is not None:
            spent_month = self._spent_since(vendor, _start_of_month())
            if spent_month + proposed_sats > rule.max_sats_per_month:
                return False, (
                    f"{proposed_sats} sats would exceed monthly limit of "
                    f"{rule.max_sats_per_month} sats for {vendor} "
                    f"({spent_month} already spent this month)"
                )

        return True, ""

    def spent_today(self, vendor: str) -> int:
        """Get total sats spent to a vendor today."""
        return self._spent_since(vendor, _start_of_day())

    def _spent_since(self, vendor: str, since: float) -> int:
        """Sum sats spent to a vendor since a timestamp."""
        rows = self.conn.execute(
            """SELECT metadata FROM memories
               WHERE type = 'transaction' AND created_at >= ?""",
            (since,),
        ).fetchall()

        total = 0
        vendor_norm = normalize_vendor(vendor)
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            meta_vendor = normalize_vendor(meta["vendor"]) if meta.get("vendor") else ""
            if meta_vendor == vendor_norm:
                total += int(meta.get("amount_sats", 0))
        return total


def _start_of_day() -> float:
    """Unix timestamp for start of today (UTC)."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def _start_of_month() -> float:
    """Unix timestamp for start of this month (UTC)."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()
