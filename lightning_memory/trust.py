"""Vendor trust and community reputation engine.

Combines local KYC status, transaction-based reputation, and
community attestations (via Nostr NIP-85 Trusted Assertions)
into a unified trust profile per vendor.
"""

from __future__ import annotations

import json
import sqlite3
import time

from .intelligence import IntelligenceEngine
from .lightning import VendorTrust
from .memory import normalize_vendor


class TrustEngine:
    """Vendor trust profiles: KYC + reputation + community attestations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set_vendor_kyc(
        self,
        vendor: str,
        verified: bool = False,
        jurisdiction: str = "",
        source: str = "",
    ) -> dict:
        """Set KYC verification status for a vendor."""
        vendor = normalize_vendor(vendor)
        now = time.time()
        self.conn.execute(
            """INSERT INTO vendor_kyc (vendor, kyc_verified, jurisdiction,
                   verification_source, verified_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(vendor) DO UPDATE SET
                   kyc_verified=excluded.kyc_verified,
                   jurisdiction=excluded.jurisdiction,
                   verification_source=excluded.verification_source,
                   verified_at=excluded.verified_at,
                   updated_at=excluded.updated_at""",
            (vendor, int(verified), jurisdiction, source,
             now if verified else None, now, now),
        )
        self.conn.commit()
        return {"vendor": vendor, "kyc_verified": verified, "jurisdiction": jurisdiction}

    def get_vendor_kyc(self, vendor: str) -> dict:
        """Get KYC status for a vendor."""
        vendor = normalize_vendor(vendor)
        row = self.conn.execute(
            "SELECT * FROM vendor_kyc WHERE vendor = ?", (vendor,)
        ).fetchone()
        if not row:
            return {"vendor": vendor, "kyc_verified": False, "jurisdiction": ""}
        return {
            "vendor": row["vendor"],
            "kyc_verified": bool(row["kyc_verified"]),
            "jurisdiction": row["jurisdiction"],
            "verification_source": row["verification_source"],
            "verified_at": row["verified_at"],
        }

    def community_reputation(self, vendor: str) -> tuple[float, int]:
        """Aggregate community reputation from synced attestation memories.

        Looks for memories of type 'attestation' referencing this vendor,
        which are synced from Nostr relays (NIP-85 Trusted Assertions).

        Returns (score 0.0-1.0, attestation_count).
        """
        rows = self.conn.execute(
            """SELECT content, metadata FROM memories
               WHERE type = 'attestation'
               ORDER BY created_at DESC""",
        ).fetchall()

        vendor_norm = normalize_vendor(vendor)
        scores: list[float] = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            target = normalize_vendor(meta["vendor"]) if meta.get("vendor") else ""
            if target != vendor_norm:
                continue
            score = meta.get("trust_score")
            if score is not None:
                scores.append(float(score))

        if not scores:
            return 0.0, 0
        return sum(scores) / len(scores), len(scores)

    def vendor_trust_profile(self, vendor: str) -> VendorTrust:
        """Build a complete trust profile for a vendor."""
        kyc = self.get_vendor_kyc(vendor)
        intel = IntelligenceEngine(self.conn)
        local_rep = intel.vendor_report(vendor)
        community_score, attestation_count = self.community_reputation(vendor)

        return VendorTrust(
            vendor=vendor,
            kyc_verified=kyc["kyc_verified"],
            jurisdiction=kyc.get("jurisdiction", ""),
            community_score=community_score,
            attestation_count=attestation_count,
            local_reputation=local_rep,
        )
