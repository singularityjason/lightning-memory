"""Lightning Memory MCP server: 14 tools for agent memory, intelligence, and sync."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .budget import BudgetEngine
from .config import load_config
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

    response = {
        "status": "stored",
        "id": result["id"],
        "type": result["type"],
        "agent_pubkey": engine.identity.public_key_hex,
    }

    # Auto-attestation: publish trust assertion after threshold transactions
    if type == "transaction" and isinstance(meta, dict) and meta.get("vendor"):
        _maybe_auto_attest(engine, meta["vendor"])

    return response


def _maybe_auto_attest(engine: MemoryEngine, vendor: str) -> None:
    """Fire auto-attestation if vendor txn count hits threshold."""
    try:
        from .sync import push_trust_assertion

        config = load_config()
        threshold = config.auto_attest_threshold
        if threshold <= 0:
            return

        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report(vendor)
        if rep.total_txns > 0 and rep.total_txns % threshold == 0:
            volume_factor = min(rep.total_txns, 20) / 20
            score = rep.success_rate * volume_factor
            push_trust_assertion(
                engine.conn, engine.identity, vendor, score, "auto_attestation",
            )
    except Exception:
        pass  # Fire-and-forget — don't fail the store


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
    from .sync import pull_memories, pull_trust_assertions, push_memories, SyncResult

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

        # Also pull trust assertions from relays
        ta_result = pull_trust_assertions(engine.conn, engine.identity)
        combined.pulled += ta_result.pulled
        combined.errors.extend(ta_result.errors)

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


@mcp.tool()
def ln_trust_attest(
    vendor: str,
    score: float | None = None,
    basis: str = "transaction_history",
) -> dict:
    """Publish a trust attestation for a vendor.

    Creates a NIP-85 Trusted Assertion and pushes it to Nostr relays.
    Other agents can pull these attestations to build community reputation.

    Args:
        vendor: Vendor name or domain to attest.
        score: Trust score 0.0-1.0. If omitted, auto-calculated from
            local reputation (success_rate * volume factor).
        basis: Reason for the score (default: "transaction_history").

    Returns:
        Attestation details including score and relay push status.
    """
    from .sync import push_trust_assertion

    # Validate manual score
    if score is not None and (score < 0.0 or score > 1.0):
        return {"error": f"score must be between 0.0 and 1.0, got {score}"}

    engine = _get_engine()

    # Auto-calculate score if not provided
    if score is None:
        intel = _get_intelligence()
        rep = intel.vendor_report(vendor)
        if rep.total_txns == 0:
            return {"error": f"No transaction history with {vendor}. Cannot auto-calculate score."}
        volume_factor = min(rep.total_txns, 20) / 20
        score = rep.success_rate * volume_factor

    result = push_trust_assertion(
        engine.conn, engine.identity, vendor, score, basis,
    )

    return {
        "status": "attested",
        "vendor": vendor,
        "score": score,
        "basis": basis,
        "pushed": result.pushed,
        "errors": result.errors,
    }


_VALID_COMPLIANCE_LEVELS = {"unknown", "self_declared", "kyc_verified", "regulated_entity"}


@mcp.tool()
def ln_agent_attest(
    agent_pubkey: str,
    owner_id: str = "",
    jurisdiction: str = "",
    compliance_level: str = "self_declared",
    source: str = "",
) -> dict:
    """Store an attestation about an agent's identity and compliance status.

    Used for Know Your Agent (KYA) — agents self-attesting, operators
    attesting their agents, or third-party KYA providers.

    Args:
        agent_pubkey: The agent's Nostr public key (64 hex chars).
        owner_id: Owner identifier (email, company name, etc.).
        jurisdiction: Legal jurisdiction (e.g., "US", "EU", "SG").
        compliance_level: One of: unknown, self_declared, kyc_verified, regulated_entity.
        source: Verification source (e.g., "manual", "sumsub", "trulioo").

    Returns:
        The stored attestation record.
    """
    import time

    if compliance_level not in _VALID_COMPLIANCE_LEVELS:
        return {"error": f"Invalid compliance_level: {compliance_level}. Must be one of: {', '.join(sorted(_VALID_COMPLIANCE_LEVELS))}"}

    engine = _get_engine()
    now = time.time()
    engine.conn.execute(
        """INSERT INTO agent_attestations
           (agent_pubkey, owner_id, jurisdiction, compliance_level, verification_source, verified_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent_pubkey) DO UPDATE SET
               owner_id=excluded.owner_id,
               jurisdiction=excluded.jurisdiction,
               compliance_level=excluded.compliance_level,
               verification_source=excluded.verification_source,
               verified_at=excluded.verified_at,
               updated_at=excluded.updated_at""",
        (agent_pubkey, owner_id, jurisdiction, compliance_level, source, now, now, now),
    )
    engine.conn.commit()

    return {
        "status": "stored",
        "agent_pubkey": agent_pubkey,
        "compliance_level": compliance_level,
        "jurisdiction": jurisdiction,
    }


@mcp.tool()
def ln_agent_verify(agent_pubkey: str) -> dict:
    """Look up an agent's compliance attestation.

    Args:
        agent_pubkey: The agent's Nostr public key to verify.

    Returns:
        Attestation details or {status: "unknown"} if not found.
    """
    engine = _get_engine()
    row = engine.conn.execute(
        "SELECT * FROM agent_attestations WHERE agent_pubkey = ?",
        (agent_pubkey,),
    ).fetchone()

    if not row:
        return {"status": "unknown", "agent_pubkey": agent_pubkey}

    return {
        "status": "verified",
        "agent_pubkey": row["agent_pubkey"],
        "owner_id": row["owner_id"],
        "jurisdiction": row["jurisdiction"],
        "compliance_level": row["compliance_level"],
        "verification_source": row["verification_source"],
        "verified_at": row["verified_at"],
    }


_VALID_SESSION_STATES = {"active", "expired", "revoked"}


@mcp.tool()
def ln_auth_session(
    vendor: str,
    linking_key: str,
    session_state: str = "active",
) -> dict:
    """Store or update an LNURL-auth session record.

    Record-keeping for externally-established LNURL-auth sessions.

    Args:
        vendor: Vendor name or domain.
        linking_key: The LNURL-auth linking key for this vendor.
        session_state: Session state: active, expired, or revoked.

    Returns:
        The stored session record.
    """
    import time

    if session_state not in _VALID_SESSION_STATES:
        return {"error": f"Invalid session_state: {session_state}. Must be one of: {', '.join(sorted(_VALID_SESSION_STATES))}"}

    engine = _get_engine()
    now = time.time()
    agent_pubkey = engine.identity.public_key_hex

    engine.conn.execute(
        """INSERT INTO auth_sessions
           (vendor, agent_pubkey, linking_key, session_state, last_auth_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(vendor, agent_pubkey) DO UPDATE SET
               linking_key=excluded.linking_key,
               session_state=excluded.session_state,
               last_auth_at=excluded.last_auth_at,
               updated_at=excluded.updated_at""",
        (vendor, agent_pubkey, linking_key, session_state, now, now, now),
    )
    engine.conn.commit()

    return {
        "status": "stored",
        "vendor": vendor,
        "agent_pubkey": agent_pubkey,
        "linking_key": linking_key,
        "session_state": session_state,
    }


@mcp.tool()
def ln_auth_lookup(vendor: str) -> dict:
    """Check if an LNURL-auth session exists with a vendor.

    Args:
        vendor: Vendor name or domain to check.

    Returns:
        Session details if found, or {has_session: false}.
    """
    engine = _get_engine()
    agent_pubkey = engine.identity.public_key_hex

    row = engine.conn.execute(
        "SELECT * FROM auth_sessions WHERE vendor = ? AND agent_pubkey = ?",
        (vendor, agent_pubkey),
    ).fetchone()

    if not row:
        return {"has_session": False, "vendor": vendor}

    return {
        "has_session": True,
        "vendor": row["vendor"],
        "linking_key": row["linking_key"],
        "session_state": row["session_state"],
        "last_auth_at": row["last_auth_at"],
    }


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
