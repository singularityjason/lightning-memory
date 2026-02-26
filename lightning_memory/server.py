"""Lightning Memory MCP server: 8 tools for agent memory, intelligence, and sync."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .intelligence import IntelligenceEngine
from .memory import MemoryEngine

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


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
