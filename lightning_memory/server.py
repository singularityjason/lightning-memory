"""Lightning Memory MCP server: 3 core tools for agent memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

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


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
