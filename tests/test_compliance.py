"""Tests for compliance report generation."""

import time

from lightning_memory.compliance import ComplianceEngine
from lightning_memory.db import store_memory


def test_compliance_report_structure(tmp_db, tmp_identity):
    """Report should have all required sections."""
    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")

    assert "agent_identity" in report
    assert "transactions" in report
    assert "budget_rules" in report
    assert "vendor_kyc" in report
    assert "anomaly_flags" in report
    assert "trust_attestations" in report
    assert report["agent_identity"]["pubkey"] == tmp_identity.public_key_hex


def test_compliance_report_transactions(tmp_db, tmp_identity):
    """Report should include transaction memories in time period."""
    store_memory(tmp_db, "t1", "Paid 100 sats", memory_type="transaction",
                 metadata={"vendor": "bitrefill.com", "amount_sats": 100})
    store_memory(tmp_db, "t2", "Paid 200 sats", memory_type="transaction",
                 metadata={"vendor": "openai.com", "amount_sats": 200})
    store_memory(tmp_db, "g1", "General note", memory_type="general")

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert len(report["transactions"]) == 2


def test_compliance_report_attestations(tmp_db, tmp_identity):
    """Report should include attestation memories."""
    store_memory(tmp_db, "a1", "Trust attestation for x.com", memory_type="attestation",
                 metadata={"vendor": "x.com", "trust_score": 0.9, "attester": "abc"})

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert len(report["trust_attestations"]) == 1


def test_compliance_report_agent_attestation(tmp_db, tmp_identity):
    """Report should include agent's own attestation if exists."""
    now = time.time()
    tmp_db.execute(
        """INSERT INTO agent_attestations
           (agent_pubkey, owner_id, jurisdiction, compliance_level, verification_source, verified_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tmp_identity.public_key_hex, "jason@e1.ai", "US", "self_declared", "manual", now, now, now),
    )
    tmp_db.commit()

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert report["agent_identity"]["compliance_level"] == "self_declared"
    assert report["agent_identity"]["jurisdiction"] == "US"
