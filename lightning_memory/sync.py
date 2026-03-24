"""Bidirectional sync between local SQLite and Nostr relays.

Push: local memories → signed NIP-78 events → relays
Pull: relay events → dedup by event ID → local DB
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .nostr import KIND_NIP78, NIP85_KIND, NostrIdentity, parse_trust_assertion
from .relay import fetch_from_relays, publish_batch_to_relays, publish_to_relays


@dataclass
class SyncResult:
    """Summary of a sync operation."""

    pushed: int = 0
    pulled: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_unsigned: int = 0

    def to_dict(self) -> dict:
        return {
            "pushed": self.pushed,
            "pulled": self.pulled,
            "errors": self.errors,
            "skipped_unsigned": self.skipped_unsigned,
        }


def _ensure_sync_schema(conn: sqlite3.Connection) -> None:
    """Create sync tracking table if needed."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            memory_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            pushed_at REAL NOT NULL,
            relay_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_cursor (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()


def _get_cursor(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM sync_cursor WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def _set_cursor(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursor (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def push_memories(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    limit: int | None = None,
) -> SyncResult:
    """Push un-synced local memories to Nostr relays as signed NIP-78 events.

    Requires secp256k1 for signing. Memories without valid signatures are skipped.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    if not identity.has_signing:
        result.errors.append(
            "Cannot push: secp256k1 not available. "
            "Install with: pip install lightning-memory[sync]"
        )
        return result

    max_events = limit or config.max_events_per_sync

    # Find memories not yet pushed
    rows = conn.execute(
        """SELECT m.id, m.content, m.type, m.metadata, m.created_at
           FROM memories m
           LEFT JOIN sync_log s ON m.id = s.memory_id
           WHERE s.memory_id IS NULL
           ORDER BY m.created_at ASC
           LIMIT ?""",
        (max_events,),
    ).fetchall()

    if not rows:
        return result

    # Build all events first, then publish in a single event loop
    events_with_ids: list[tuple[str, dict]] = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else None
        event = identity.create_memory_event(
            content=row["content"],
            memory_type=row["type"],
            memory_id=row["id"],
            metadata=meta,
            sign=True,
        )
        events_with_ids.append((row["id"], event))

    try:
        batch_results = asyncio.run(
            publish_batch_to_relays(
                config.relays,
                [ev for _, ev in events_with_ids],
                config.sync_timeout_seconds,
            )
        )
        for (memory_id, _event), (_ev, responses) in zip(events_with_ids, batch_results):
            success_count = sum(1 for r in responses if r.success)
            if success_count > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_log (memory_id, event_id, pushed_at, relay_count) "
                    "VALUES (?, ?, ?, ?)",
                    (memory_id, _event["id"], time.time(), success_count),
                )
                conn.commit()
                result.pushed += 1
            else:
                errors = [f"{r.relay}: {r.message}" for r in responses if not r.success]
                result.errors.extend(errors)
    except Exception as e:
        result.errors.append(f"Batch push failed: {e}")

    return result


def pull_memories(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
) -> SyncResult:
    """Pull memories from Nostr relays authored by this identity.

    Fetches NIP-78 events and imports new ones into the local DB.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    # Build filter for our events
    filters: dict[str, Any] = {
        "kinds": [KIND_NIP78],
        "authors": [identity.public_key_hex],
        "limit": config.max_events_per_sync,
    }

    # Use sync cursor to only fetch newer events
    last_sync = _get_cursor(conn, "last_pull_timestamp")
    if last_sync:
        filters["since"] = int(float(last_sync))

    try:
        responses = asyncio.run(
            fetch_from_relays(config.relays, filters, config.sync_timeout_seconds)
        )
    except Exception as e:
        result.errors.append(f"Fetch failed: {e}")
        return result

    # Collect and dedup events across relays
    seen_ids: set[str] = set()
    events: list[dict] = []
    for resp in responses:
        if not resp.success:
            result.errors.append(f"{resp.relay}: {resp.message}")
            continue
        for event in resp.events:
            eid = event.get("id", "")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                # Skip events with type tags (KYA, gateway) — not memory events
                type_tag = _extract_tag(event, "type")
                if type_tag in ("kya", "gateway"):
                    continue
                events.append(event)

    # Import events into local DB
    latest_ts = 0
    for event in events:
        # Skip if we already have this event
        existing = conn.execute(
            "SELECT id FROM memories WHERE nostr_event_id = ?", (event["id"],)
        ).fetchone()
        if existing:
            continue

        # Parse event back into memory fields
        memory_id = _extract_memory_id(event)
        content = event.get("content", "")
        memory_type = _extract_tag(event, "t") or "general"
        metadata_str = _extract_tag(event, "metadata")
        metadata = json.loads(metadata_str) if metadata_str else {}

        from .db import store_memory
        store_memory(
            conn,
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
            nostr_event_id=event["id"],
        )
        result.pulled += 1

        if event.get("created_at", 0) > latest_ts:
            latest_ts = event["created_at"]

    if latest_ts > 0:
        _set_cursor(conn, "last_pull_timestamp", str(latest_ts))

    return result


def pull_trust_assertions(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
) -> SyncResult:
    """Pull NIP-85 trust assertion events from relays.

    Fetches kind 30382 events for vendors in local transaction history.
    Stores valid assertions as memories with type='attestation'.
    Rejects scores outside 0.0-1.0 range.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    # Get vendors from local transaction history
    rows = conn.execute(
        "SELECT DISTINCT metadata FROM memories WHERE type = 'transaction'"
    ).fetchall()
    vendors: set[str] = set()
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        v = meta.get("vendor", "")
        if v:
            vendors.add(v.lower())

    if not vendors:
        return result

    # Query relays for trust assertions about known vendors (batched)
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    async def _fetch_all_vendor_assertions() -> list[tuple[str, list]]:
        """Fetch trust assertions for all vendors in a single event loop."""
        tasks = []
        vendor_list = sorted(vendors)
        for vendor in vendor_list:
            filters: dict[str, Any] = {
                "kinds": [NIP85_KIND],
                "#d": [f"trust:{vendor}"],
                "limit": 50,
            }
            tasks.append(fetch_from_relays(config.relays, filters, config.sync_timeout_seconds))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return list(zip(vendor_list, results))

    try:
        vendor_results = asyncio.run(_fetch_all_vendor_assertions())
    except Exception as e:
        result.errors.append(f"Batch fetch failed: {e}")
        vendor_results = []

    for vendor, responses in vendor_results:
        if isinstance(responses, Exception):
            result.errors.append(f"Fetch failed for {vendor}: {responses}")
            continue
        for resp in responses:
            if not resp.success:
                result.errors.append(f"{resp.relay}: {resp.message}")
                continue
            for event in resp.events:
                eid = event.get("id", "")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_events.append(event)

    # Import valid attestations
    for event in all_events:
        # Skip if already stored
        existing = conn.execute(
            "SELECT id FROM memories WHERE nostr_event_id = ?", (event["id"],)
        ).fetchone()
        if existing:
            continue

        # Parse and validate
        parsed = parse_trust_assertion(event)
        if parsed is None:
            continue  # Invalid or out-of-range score

        from .db import store_memory
        store_memory(
            conn,
            memory_id=f"att_{event['id'][:12]}",
            content=f"Trust attestation for {parsed['vendor']}: score {parsed['trust_score']}",
            memory_type="attestation",
            metadata={
                "vendor": parsed["vendor"],
                "trust_score": parsed["trust_score"],
                "attester": parsed["attester"],
                "basis": parsed["basis"],
            },
            nostr_event_id=event["id"],
        )
        result.pulled += 1

    return result


def push_trust_assertion(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    vendor: str,
    score: float,
    basis: str = "transaction_history",
) -> SyncResult:
    """Create and publish a NIP-85 trust assertion to relays.

    Also stores the attestation locally as a memory.
    Requires secp256k1 for signing.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    if not identity.has_signing:
        result.errors.append(
            "Cannot push: secp256k1 not available. "
            "Install with: pip install lightning-memory[sync]"
        )
        return result

    event = identity.create_trust_assertion_event(
        vendor=vendor, score=score, basis=basis, sign=True,
    )

    try:
        responses = asyncio.run(
            publish_to_relays(config.relays, event, config.sync_timeout_seconds)
        )
        success_count = sum(1 for r in responses if r.success)
        if success_count > 0:
            result.pushed = 1
        else:
            errors = [f"{r.relay}: {r.message}" for r in responses if not r.success]
            result.errors.extend(errors)
    except Exception as e:
        result.errors.append(f"Push failed: {e}")

    # Store locally regardless of push success
    from .db import store_memory
    store_memory(
        conn,
        memory_id=f"att_{event['id'][:12]}",
        content=f"Trust attestation for {vendor}: score {score}",
        memory_type="attestation",
        metadata={
            "vendor": vendor,
            "trust_score": score,
            "attester": identity.public_key_hex,
            "basis": basis,
        },
        nostr_event_id=event["id"],
    )

    return result


def push_gateway_announcement(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    gateway_url: str,
    operations: dict[str, int] | None = None,
    relays_list: list[str] | None = None,
) -> SyncResult:
    """Create and publish a NIP-78 gateway announcement to relays.

    Requires secp256k1 for signing.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    if not identity.has_signing:
        result.errors.append(
            "Cannot push: secp256k1 not available. "
            "Install with: pip install lightning-memory[sync]"
        )
        return result

    event = identity.create_gateway_announcement_event(
        gateway_url=gateway_url,
        operations=operations or config.pricing,
        relays=relays_list or config.relays,
        sign=True,
    )

    try:
        responses = asyncio.run(
            publish_to_relays(config.relays, event, config.sync_timeout_seconds)
        )
        success_count = sum(1 for r in responses if r.success)
        if success_count > 0:
            result.pushed = 1
        else:
            errors = [f"{r.relay}: {r.message}" for r in responses if not r.success]
            result.errors.extend(errors)
    except Exception as e:
        result.errors.append(f"Push failed: {e}")

    return result


def pull_gateway_announcements(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
) -> SyncResult:
    """Pull gateway announcement events from relays.

    Fetches kind 30078 events with type:gateway tag, stores in known_gateways.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    filters: dict[str, Any] = {
        "kinds": [KIND_NIP78],
        "limit": config.max_events_per_sync,
    }

    try:
        responses = asyncio.run(
            fetch_from_relays(config.relays, filters, config.sync_timeout_seconds)
        )
    except Exception as e:
        result.errors.append(f"Fetch failed: {e}")
        return result

    seen_ids: set[str] = set()
    for resp in responses:
        if not resp.success:
            result.errors.append(f"{resp.relay}: {resp.message}")
            continue
        for event in resp.events:
            eid = event.get("id", "")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)

            # Only process gateway announcements
            type_tag = _extract_tag(event, "type")
            if type_tag != "gateway":
                continue

            try:
                content = json.loads(event.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            url = content.get("url", "")
            if not url:
                continue

            pubkey = event.get("pubkey", "")
            operations = json.dumps(content.get("operations", {}))
            relays_json = json.dumps(content.get("relays", []))
            now = time.time()

            conn.execute(
                "INSERT INTO known_gateways "
                "(agent_pubkey, url, operations, relays, nostr_event_id, last_seen, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_pubkey) DO UPDATE SET "
                "url=excluded.url, operations=excluded.operations, relays=excluded.relays, "
                "nostr_event_id=excluded.nostr_event_id, last_seen=excluded.last_seen",
                (pubkey, url, operations, relays_json, eid, now, now),
            )
            conn.commit()
            result.pulled += 1

    return result


def export_memories(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    limit: int = 100,
) -> list[dict]:
    """Export local memories as NIP-78 events (signed if possible).

    Returns a list of event dicts suitable for sharing or relay publishing.
    """
    rows = conn.execute(
        "SELECT id, content, type, metadata FROM memories ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    events = []
    can_sign = identity.has_signing
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else None
        event = identity.create_memory_event(
            content=row["content"],
            memory_type=row["type"],
            memory_id=row["id"],
            metadata=meta,
            sign=can_sign,
        )
        events.append(event)

    return events


def _extract_memory_id(event: dict) -> str:
    """Extract memory ID from NIP-78 'd' tag."""
    d_tag = _extract_tag(event, "d")
    if d_tag and d_tag.startswith("lm:"):
        return d_tag[3:]
    # Fallback: use event ID truncated
    return event.get("id", "unknown")[:16]


def _extract_tag(event: dict, tag_name: str) -> str | None:
    """Extract the first value for a given tag name from event tags."""
    for tag in event.get("tags", []):
        if len(tag) >= 2 and tag[0] == tag_name:
            return tag[1]
    return None
