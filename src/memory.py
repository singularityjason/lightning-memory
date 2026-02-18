"""Memory engine: store, query, list operations with Nostr-aware identity."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any

from . import db
from .nostr import NostrIdentity


class MemoryEngine:
    """Lightweight memory engine backed by SQLite with Nostr identity."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        identity: NostrIdentity | None = None,
    ):
        self.conn = conn or db.get_connection()
        self.identity = identity or NostrIdentity.load_or_create()

    def store(
        self,
        content: str,
        memory_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a memory. Returns the stored memory record.

        Types: general, transaction, vendor, preference, error, decision
        """
        memory_id = self._generate_id(content)
        meta = metadata or {}
        meta["agent_pubkey"] = self.identity.public_key_hex

        result = db.store_memory(
            self.conn,
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            metadata=meta,
        )

        return result

    def query(
        self,
        query: str,
        limit: int = 10,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query memories by relevance. Uses FTS5 BM25 ranking.

        Falls back to substring match if FTS5 query fails (e.g. special characters).
        """
        try:
            results = db.query_memories(self.conn, query, limit, memory_type)
        except Exception:
            # Fallback: simple LIKE query for robustness
            results = self._fallback_query(query, limit, memory_type)
        return results

    def list(
        self,
        memory_type: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List memories, filtered by type and/or time.

        Args:
            memory_type: Filter by type (e.g. "transaction", "vendor")
            since: ISO 8601 timestamp or relative like "1h", "24h", "7d"
            limit: Max results
        """
        since_ts = self._parse_since(since) if since else None
        return db.list_memories(self.conn, memory_type, since_ts, limit)

    def stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        counts = db.count_memories(self.conn)
        return {
            "agent_pubkey": self.identity.public_key_hex,
            **counts,
        }

    def _generate_id(self, content: str) -> str:
        """Generate a deterministic ID from content + timestamp."""
        raw = f"{content}:{time.time()}:{self.identity.public_key_hex}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _parse_since(self, since: str) -> float:
        """Parse a relative time string like '1h', '24h', '7d' into a Unix timestamp."""
        now = time.time()
        since = since.strip().lower()

        if since.endswith("h"):
            hours = float(since[:-1])
            return now - (hours * 3600)
        elif since.endswith("d"):
            days = float(since[:-1])
            return now - (days * 86400)
        elif since.endswith("m"):
            minutes = float(since[:-1])
            return now - (minutes * 60)
        else:
            # Try parsing as a Unix timestamp
            try:
                return float(since)
            except ValueError:
                # Default: last 24 hours
                return now - 86400

    def _fallback_query(
        self, query: str, limit: int, memory_type: str | None
    ) -> list[dict[str, Any]]:
        """Simple LIKE-based fallback when FTS5 fails."""
        import json

        conditions = ["content LIKE ?"]
        params: list[Any] = [f"%{query}%"]

        if memory_type:
            conditions.append("type = ?")
            params.append(memory_type)

        params.append(limit)
        where = " AND ".join(conditions)

        rows = self.conn.execute(
            f"""SELECT id, content, type, metadata, nostr_event_id, created_at
                FROM memories WHERE {where}
                ORDER BY created_at DESC LIMIT ?""",
            params,
        )

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "type": row["type"],
                "metadata": json.loads(row["metadata"]),
                "nostr_event_id": row["nostr_event_id"],
                "created_at": row["created_at"],
                "relevance": 0.5,
            }
            for row in rows
        ]
