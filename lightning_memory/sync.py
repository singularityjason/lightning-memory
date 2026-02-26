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
from .nostr import KIND_NIP78, NostrIdentity
from .relay import fetch_from_relays, publish_to_relays


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

    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else None
        event = identity.create_memory_event(
            content=row["content"],
            memory_type=row["type"],
            memory_id=row["id"],
            metadata=meta,
            sign=True,
        )

        try:
            responses = asyncio.run(
                publish_to_relays(config.relays, event, config.sync_timeout_seconds)
            )
            success_count = sum(1 for r in responses if r.success)
            if success_count > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_log (memory_id, event_id, pushed_at, relay_count) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], event["id"], time.time(), success_count),
                )
                conn.commit()
                result.pushed += 1
            else:
                errors = [f"{r.relay}: {r.message}" for r in responses if not r.success]
                result.errors.extend(errors)
        except Exception as e:
            result.errors.append(f"Push failed for {row['id']}: {e}")

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
