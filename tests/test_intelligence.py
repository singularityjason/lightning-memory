"""Tests for the Lightning intelligence engine."""

import json

from lightning_memory.intelligence import IntelligenceEngine, _extract_amount


def _seed_transactions(engine, txns):
    """Store a batch of transaction memories via the engine."""
    for txn in txns:
        engine.store(
            content=txn["content"],
            memory_type="transaction",
            metadata=txn.get("metadata", {}),
        )


class TestVendorReport:
    def test_no_transactions(self, engine):
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("unknown-vendor")
        assert rep.total_txns == 0
        assert rep.total_sats == 0
        assert rep.success_rate == 1.0

    def test_single_vendor(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 500 sats to bitrefill", "metadata": {"vendor": "bitrefill", "amount_sats": 500}},
            {"content": "Paid 300 sats to bitrefill", "metadata": {"vendor": "bitrefill", "amount_sats": 300}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("bitrefill")

        assert rep.total_txns == 2
        assert rep.total_sats == 800
        assert rep.avg_sats == 400.0
        assert rep.success_rate == 1.0

    def test_vendor_with_failures(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 100 sats to flaky-api", "metadata": {"vendor": "flaky-api", "amount_sats": 100}},
            {"content": "Payment to flaky-api failed with timeout", "metadata": {"vendor": "flaky-api", "amount_sats": 200}},
            {"content": "Paid 150 sats to flaky-api", "metadata": {"vendor": "flaky-api", "amount_sats": 150}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("flaky-api")

        assert rep.total_txns == 3
        assert rep.success_rate < 1.0
        assert "has_failures" in rep.tags

    def test_vendor_match_in_content(self, engine):
        """Vendor not in metadata but mentioned in content."""
        _seed_transactions(engine, [
            {"content": "Bought gift card from bitrefill for 500 sats", "metadata": {"amount_sats": 500}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("bitrefill")
        assert rep.total_txns == 1

    def test_case_insensitive(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid to BitRefill", "metadata": {"vendor": "BitRefill", "amount_sats": 100}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("bitrefill")
        assert rep.total_txns == 1

    def test_to_dict(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 100 sats", "metadata": {"vendor": "test", "amount_sats": 100}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report("test")
        d = rep.to_dict()
        assert isinstance(d, dict)
        assert d["vendor"] == "test"
        assert d["total_txns"] == 1


class TestSpendingSummary:
    def test_empty(self, engine):
        intel = IntelligenceEngine(conn=engine.conn)
        summary = intel.spending_summary("30d")
        assert summary.total_sats == 0
        assert summary.txn_count == 0

    def test_aggregation(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 500 sats", "metadata": {"vendor": "vendor-a", "amount_sats": 500, "protocol": "l402"}},
            {"content": "Paid 300 sats", "metadata": {"vendor": "vendor-b", "amount_sats": 300, "protocol": "lightning"}},
            {"content": "Paid 200 sats", "metadata": {"vendor": "vendor-a", "amount_sats": 200, "protocol": "l402"}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        summary = intel.spending_summary("30d")

        assert summary.total_sats == 1000
        assert summary.txn_count == 3
        assert summary.by_vendor["vendor-a"] == 700
        assert summary.by_vendor["vendor-b"] == 300
        assert summary.by_protocol["l402"] == 700
        assert summary.by_protocol["lightning"] == 300

    def test_to_dict(self, engine):
        intel = IntelligenceEngine(conn=engine.conn)
        summary = intel.spending_summary("7d")
        d = summary.to_dict()
        assert d["period"] == "7d"
        assert isinstance(d["by_vendor"], dict)


class TestAnomalyCheck:
    def test_first_time_vendor(self, engine):
        intel = IntelligenceEngine(conn=engine.conn)
        report = intel.anomaly_check("new-vendor", 1000)
        assert report.verdict == "first_time"
        assert "No prior transactions" in report.context

    def test_normal_amount(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 500 sats", "metadata": {"vendor": "stable", "amount_sats": 500}},
            {"content": "Paid 600 sats", "metadata": {"vendor": "stable", "amount_sats": 600}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        report = intel.anomaly_check("stable", 700)
        assert report.verdict == "normal"
        assert report.avg_historical_sats > 0

    def test_high_amount(self, engine):
        _seed_transactions(engine, [
            {"content": "Paid 100 sats", "metadata": {"vendor": "cheap", "amount_sats": 100}},
            {"content": "Paid 120 sats", "metadata": {"vendor": "cheap", "amount_sats": 120}},
        ])
        intel = IntelligenceEngine(conn=engine.conn)
        # 1000 is >3x the ~110 avg
        report = intel.anomaly_check("cheap", 1000)
        assert report.verdict == "high"
        assert "1000" in report.context

    def test_to_dict(self, engine):
        intel = IntelligenceEngine(conn=engine.conn)
        report = intel.anomaly_check("any", 100)
        d = report.to_dict()
        assert "verdict" in d
        assert "vendor" in d


class TestExtractAmount:
    def test_from_metadata(self):
        assert _extract_amount({"amount_sats": 500}, "") == 500

    def test_from_metadata_string(self):
        assert _extract_amount({"amount_sats": "300"}, "") == 300

    def test_from_content(self):
        assert _extract_amount({}, "paid 1000 sats to vendor") == 1000

    def test_from_content_singular(self):
        assert _extract_amount({}, "cost was 42 sat") == 42

    def test_no_amount(self):
        assert _extract_amount({}, "no amount here") == 0

    def test_metadata_takes_priority(self):
        assert _extract_amount({"amount_sats": 500}, "paid 1000 sats") == 500
