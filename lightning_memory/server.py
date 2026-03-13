"""Lightning Memory MCP server: 13 tools for agent memory, intelligence, and sync."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .budget import BudgetEngine
from .intelligence import IntelligenceEngine
from .memory import MemoryEngine
from .preflight import PreflightEngine
from .trust import TrustEngine

mcp = FastMCP(
    "Lightning Memory",
    instructions=(
        "Decentralized agent memory for the Lightning economy. "
        "Store, query, and list memories with Nostr identity and Lightning payments. "
        "Agents remember transactions, vendor reputations, spending patterns, and decisions."
    ),
)

# Lazy-init engine (created on first tool call)
_engine: MemoryEngine | None = None


def _get_engine() -> MemoryEngine:
    global _engine
    if _engine is None:
        _engine = MemoryEngine()
    return _engine


@mcp.tool()
def memory_store(
    content: str,
    type: str = "general",
    metadata: str = "{}",
) -> dict:
    """Store a memory for later retrieval.

    Use this to remember transactions, vendor experiences, decisions,
    spending patterns, API responses, or any information worth recalling.

    Args:
        content: The memory content to store. Be descriptive.
            Examples:
            - "Paid 500 sats to bitrefill.com for a $5 Amazon gift card via L402. Fast, reliable."
            - "OpenAI API returned 429 rate limit after 50 requests/min. Backoff to 30/min."
            - "User prefers to cap spending at 10,000 sats per session."
        type: Memory category. One of:
            - general: Default, uncategorized
            - transaction: Payment records, invoices, L402 purchases
            - vendor: Service/API reputation and reliability notes
            - preference: User or agent preferences and settings
            - error: Error patterns and failure modes
            - decision: Key decisions and their reasoning
        metadata: JSON string of additional key-value pairs.
            Example: '{"vendor": "bitrefill.com", "amount_sats": 500}'

    Returns:
        The stored memory record with id, content, type, and timestamps.
    """
    import json

    engine = _get_engine()
    meta = json.loads(metadata) if isinstance(metadata, str) else metadata
    result = engine.store(content=content, memory_type=type, metadata=meta)
    return {
        "status": "stored",
        "id": result["id"],
        "type": result["type"],
        "agent_pubkey": engine.identity.public_key_hex,
    }


@mcp.tool()
def memory_query(
    query: str,
    limit: int = 10,
    type: str | None = None,
) -> dict:
    """Search memories by relevance. Returns the most relevant matches.

    Use this to recall past transactions, check vendor reputation,
    retrieve spending patterns, or find any previously stored information.

    Args:
        query: Natural language search query.
            Examples:
            - "bitrefill payment history"
            - "which APIs gave rate limit errors?"
            - "spending decisions this week"
        limit: Maximum number of results (default 10, max 100).
        type: Optional filter by memory type (transaction, vendor, preference, error, decision, general).

    Returns:
        List of matching memories ranked by relevance, with scores.
    """
    engine = _get_engine()
    limit = min(limit, 100)
    results = engine.query(query=query, limit=limit, memory_type=type)
    return {
        "count": len(results),
        "memories": results,
    }


@mcp.tool()
def memory_list(
    type: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> dict:
    """List memories, optionally filtered by type and time range.

    Use this to browse recent memories, check all transactions,
    or review memories of a specific type.

    Args:
        type: Filter by memory type (transaction, vendor, preference, error, decision, general).
        since: Time filter. Relative: "1h", "24h", "7d", "30d". Or Unix timestamp.
        limit: Maximum number of results (default 50, max 200).

    Returns:
        List of memories in reverse chronological order with stats.
    """
    engine = _get_engine()
    limit = min(limit, 200)
    results = engine.list(memory_type=type, since=since, limit=limit)
    stats = engine.stats()
    return {
        "count": len(results),
        "total_memories": stats["total"],
        "by_type": stats["by_type"],
        "agent_pubkey": stats["agent_pubkey"],
        "memories": results,
    }


def _get_intelligence() -> IntelligenceEngine:
    engine = _get_engine()
    return IntelligenceEngine(conn=engine.conn)


def _get_budget() -> BudgetEngine:
    return BudgetEngine(conn=_get_engine().conn)


def _get_trust() -> TrustEngine:
    return TrustEngine(conn=_get_engine().conn)


def _get_preflight() -> PreflightEngine:
    return PreflightEngine(conn=_get_engine().conn)


@mcp.tool()
def ln_vendor_reputation(vendor: str) -> dict:
    """Check a vendor's reputation based on transaction history.

    Use this before paying a vendor to see if they're reliable.
    Aggregates all past transactions to build a reputation score.

    Args:
        vendor: Vendor name or domain (e.g., "bitrefill.com", "openai").

    Returns:
        Reputation report: total transactions, total sats spent,
        success rate, average payment size, and tags.
    """
    intel = _get_intelligence()
    rep = intel.vendor_report(vendor)
    return {
        "reputation": rep.to_dict(),
        "recommendation": (
            "reliable" if rep.success_rate >= 0.9 and rep.total_txns >= 3
            else "new" if rep.total_txns == 0
            else "caution" if rep.success_rate < 0.7
            else "limited_data"
        ),
    }


@mcp.tool()
def ln_spending_summary(since: str = "30d") -> dict:
    """Get a spending summary for budget awareness.

    Shows total sats spent, broken down by vendor and protocol.

    Args:
        since: Time period. Relative: "1h", "24h", "7d", "30d". Or Unix timestamp.

    Returns:
        Spending breakdown with totals by vendor and protocol.
    """
    intel = _get_intelligence()
    summary = intel.spending_summary(since)
    return {"summary": summary.to_dict()}


@mcp.tool()
def ln_anomaly_check(vendor: str, amount_sats: int) -> dict:
    """Check if a proposed payment amount is normal for a vendor.

    Use this before making a payment to catch price anomalies.
    Compares the proposed amount against historical averages.

    Args:
        vendor: Vendor name or domain.
        amount_sats: Proposed payment amount in satoshis.

    Returns:
        Anomaly report: verdict (normal/high/first_time), context, and historical average.
    """
    intel = _get_intelligence()
    report = intel.anomaly_check(vendor, amount_sats)
    return {"anomaly": report.to_dict()}


@mcp.tool()
def memory_sync(direction: str = "both") -> dict:
    """Sync memories with Nostr relays.

    Push local memories to relays and/or pull remote memories to local.
    Requires secp256k1 for push (signing). Pull works with any identity.

    Args:
        direction: Sync direction. One of:
            - "push": Upload local memories to relays
            - "pull": Download memories from relays
            - "both": Push then pull (default)

    Returns:
        Sync result with counts of pushed/pulled memories and any errors.
    """
    from .sync import pull_memories, push_memories, SyncResult

    engine = _get_engine()
    combined = SyncResult()

    if direction in ("push", "both"):
        push_result = push_memories(engine.conn, engine.identity)
        combined.pushed = push_result.pushed
        combined.errors.extend(push_result.errors)

    if direction in ("pull", "both"):
        pull_result = pull_memories(engine.conn, engine.identity)
        combined.pulled = pull_result.pulled
        combined.errors.extend(pull_result.errors)

    return {
        "status": "completed",
        "direction": direction,
        **combined.to_dict(),
    }


@mcp.tool()
def memory_export(limit: int = 100) -> dict:
    """Export memories as Nostr NIP-78 events.

    Converts local memories into portable Nostr event format.
    Events are signed if secp256k1 is available.
    Useful for backup, sharing, or manual relay publishing.

    Args:
        limit: Maximum number of memories to export (default 100).

    Returns:
        List of NIP-78 events with memory content.
    """
    from .sync import export_memories

    engine = _get_engine()
    limit = min(limit, 1000)
    events = export_memories(engine.conn, engine.identity, limit)
    return {
        "count": len(events),
        "signed": engine.identity.has_signing,
        "agent_pubkey": engine.identity.public_key_hex,
        "events": events,
    }


@mcp.tool()
def ln_budget_status() -> dict:
    """Check L402 gateway earnings and payment stats.

    Shows total sats earned from L402 gateway payments, broken down by operation.
    Reads from locally stored payment records (logged by the gateway).

    Returns:
        Earnings summary: total sats, payment count, breakdown by operation.
    """
    import json as _json

    engine = _get_engine()
    payments = engine.list(memory_type="l402_payment", limit=1000)
    total_sats = 0
    by_operation: dict[str, int] = {}
    for p in payments:
        meta = p.get("metadata", {})
        if isinstance(meta, str):
            meta = _json.loads(meta) if meta else {}
        sats = meta.get("amount_sats", 0)
        total_sats += sats
        op = meta.get("operation", "unknown")
        by_operation[op] = by_operation.get(op, 0) + sats
    return {
        "total_earned_sats": total_sats,
        "total_payments": len(payments),
        "by_operation": by_operation,
    }


@mcp.tool()
def ln_budget_set(
    vendor: str,
    max_sats_per_txn: int | None = None,
    max_sats_per_day: int | None = None,
    max_sats_per_month: int | None = None,
) -> dict:
    """Set spending limits for a vendor.

    Creates budget rules that the pre-flight gate enforces.
    Any payment exceeding these limits will be rejected.

    Args:
        vendor: Vendor name or domain (e.g., "bitrefill.com").
        max_sats_per_txn: Maximum sats allowed per single transaction.
        max_sats_per_day: Maximum total sats per day to this vendor.
        max_sats_per_month: Maximum total sats per month to this vendor.

    Returns:
        The created/updated budget rule.
    """
    budget = _get_budget()
    rule = budget.set_rule(
        vendor, max_sats_per_txn=max_sats_per_txn,
        max_sats_per_day=max_sats_per_day, max_sats_per_month=max_sats_per_month,
    )
    return {"status": "set", "rule": rule.to_dict()}


@mcp.tool()
def ln_budget_check(vendor: str | None = None) -> dict:
    """List budget rules and current spending status.

    Shows all active budget rules, or details for a specific vendor
    including how much has been spent today and this month.

    Args:
        vendor: Optional vendor to check. If omitted, lists all rules.

    Returns:
        Budget rules with current spending against limits.
    """
    budget = _get_budget()
    if vendor:
        rule = budget.get_rule(vendor)
        if not rule:
            return {"vendor": vendor, "has_rule": False}
        return {
            "vendor": vendor,
            "has_rule": True,
            "rule": rule.to_dict(),
            "spent_today": budget.spent_today(vendor),
        }
    rules = budget.list_rules()
    return {
        "count": len(rules),
        "rules": [r.to_dict() for r in rules],
    }


@mcp.tool()
def ln_vendor_trust(vendor: str) -> dict:
    """Get a vendor's full trust profile.

    Combines KYC verification status, local transaction reputation,
    and community trust attestations (from Nostr NIP-85) into a
    unified trust profile.

    Args:
        vendor: Vendor name or domain.

    Returns:
        Trust profile: KYC status, jurisdiction, community score,
        attestation count, and local reputation data.
    """
    trust = _get_trust()
    profile = trust.vendor_trust_profile(vendor)
    return {"trust": profile.to_dict()}


@mcp.tool()
def ln_preflight(vendor: str, amount_sats: int) -> dict:
    """Pre-flight check before making a payment.

    Runs budget limits, anomaly detection, and trust verification
    to produce an approve/reject/escalate decision. Use this before
    every payment to catch overspending, price anomalies, and
    unverified vendors.

    Args:
        vendor: Vendor name or domain.
        amount_sats: Proposed payment amount in satoshis.

    Returns:
        Decision: verdict (approve/reject/escalate), reasons,
        budget remaining, anomaly status, and trust score.
    """
    pf = _get_preflight()
    decision = pf.check(vendor, amount_sats)
    return {"decision": decision.to_dict()}


def _cmd_relay_status() -> None:
    """Show connection status for configured Nostr relays."""
    import asyncio
    import time

    from .config import load_config
    from .relay import check_relays

    config = load_config()
    relays = config.relays
    print(f"Checking {len(relays)} relay(s)...\n")

    start = time.monotonic()
    results = asyncio.run(check_relays(relays))
    elapsed = time.monotonic() - start

    ok_count = 0
    for r in results:
        status = "OK" if r.success else "FAIL"
        icon = "+" if r.success else "x"
        msg = f"  [{icon}] {r.relay}: {status}"
        if r.message and r.message != "connected":
            msg += f" ({r.message})"
        print(msg)
        if r.success:
            ok_count += 1

    print(f"\n{ok_count}/{len(relays)} relays reachable ({elapsed:.1f}s)")

    # Show last sync info if available
    try:
        engine = _get_engine()
        from .sync import _ensure_sync_schema, _get_cursor
        _ensure_sync_schema(engine.conn)
        last_pull = _get_cursor(engine.conn, "last_pull_timestamp")
        if last_pull:
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(float(last_pull), tz=timezone.utc)
            print(f"Last pull: {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        synced = engine.conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
        if synced:
            print(f"Memories pushed: {synced}")
    except Exception:
        pass


def main():
    """Run the MCP server, or a CLI subcommand."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "relay-status":
        _cmd_relay_status()
        return

    mcp.run()


if __name__ == "__main__":
    main()
