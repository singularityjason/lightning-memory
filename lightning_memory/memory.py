"""Memory engine: store, query, list operations with Nostr-aware identity."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from . import db
from .nostr import NostrIdentity


def normalize_vendor(name: str) -> str:
    """Normalize a vendor name for consistent matching.

    Strips protocol, www prefix, trailing slashes, and lowercases.
    Examples:
        "https://www.Bitrefill.com/" -> "bitrefill.com"
        "WWW.BITREFILL.COM" -> "bitrefill.com"
        "bitrefill.com" -> "bitrefill.com"
    """
    v = name.strip().lower()
    # Strip protocol
    for prefix in ("https://", "http://"):
        if v.startswith(prefix):
            v = v[len(prefix):]
            break
    # Strip www. prefix
    if v.startswith("www."):
        v = v[4:]
    # Strip trailing slash
    v = v.rstrip("/")
    return v


def parse_since(since: str) -> float:
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
        try:
            return float(since)
        except ValueError:
            return now - 86400


# Per-type similarity thresholds for dedup
_DEDUP_THRESHOLDS: dict[str, float] = {
    "transaction": 0.85,  # higher — different amounts should not dedup
    "vendor": 0.80,
    "general": 0.80,
    "preference": 0.80,
    "error": 0.70,  # errors often restate the same issue differently
    "decision": 0.80,
}
_DEDUP_DEFAULT_THRESHOLD = 0.80


def _jaccard(text_a: str, text_b: str, min_word_len: int = 3) -> float:
    """Compute Jaccard similarity on word sets (words >= min_word_len chars)."""
    words_a = {re.sub(r"[^\w]", "", w) for w in text_a.lower().split() if len(w) >= min_word_len}
    words_b = {re.sub(r"[^\w]", "", w) for w in text_b.lower().split() if len(w) >= min_word_len}
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


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

        Deduplicates against recent memories of the same type using
        Jaccard word-set similarity. If a near-duplicate exists,
        returns the existing memory with a ``dedup`` flag.

        Types: general, transaction, vendor, preference, error, decision
        """
        # Check for near-duplicate before storing
        existing = self._find_duplicate(content, memory_type, metadata)
        if existing is not None:
            existing["dedup"] = True
            return existing

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

        # Generate and store embedding (non-blocking — failure doesn't affect store)
        try:
            from .embedding import has_embeddings, generate_embedding
            if has_embeddings():
                vec = generate_embedding(content)
                db.store_embedding(self.conn, memory_id, vec)
        except Exception:
            pass  # Embedding failure should never block memory storage

        # Check for contradictions with existing memories
        contradictions = self._detect_contradictions(content, memory_type, metadata)
        if contradictions:
            result["contradictions"] = contradictions

        return result

    def query(
        self,
        query: str,
        limit: int = 10,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query memories by relevance.

        When embeddings are available, runs both FTS5 keyword search and
        cosine similarity search, then merges results by rank fusion.
        Falls back to FTS5-only (or substring match) when embeddings are unavailable.
        """
        # FTS5 keyword results
        try:
            fts_results = db.query_memories(self.conn, query, limit, memory_type)
        except Exception:
            fts_results = self._fallback_query(query, limit, memory_type)

        # Try semantic search if embeddings are available
        try:
            from .embedding import has_embeddings, generate_embedding
            if has_embeddings():
                query_vec = generate_embedding(query)
                sem_results = db.query_by_embedding(
                    self.conn, query_vec, limit, memory_type,
                )
                return self._merge_results(fts_results, sem_results, limit)
        except Exception:
            pass  # Fall through to FTS5-only results

        return fts_results

    @staticmethod
    def _merge_results(
        fts_results: list[dict[str, Any]],
        sem_results: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Merge FTS5 and semantic results using reciprocal rank fusion."""
        scores: dict[str, float] = {}
        by_id: dict[str, dict[str, Any]] = {}

        k = 60  # RRF constant

        for rank, r in enumerate(fts_results):
            mid = r["id"]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
            by_id[mid] = r

        for rank, r in enumerate(sem_results):
            mid = r["id"]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
            if mid not in by_id:
                by_id[mid] = r

        # Sort by fused score, highest first
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for mid, score in ranked[:limit]:
            entry = by_id[mid]
            entry["relevance"] = round(score, 4)
            results.append(entry)

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

    def edit(
        self,
        memory_id: str,
        new_content: str | None = None,
        new_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit a memory's content and/or metadata.

        Tracks edit history via edited_at and edit_count in metadata.
        Returns the updated memory record or an error dict.
        """
        # Get current state
        row = self.conn.execute(
            "SELECT content, metadata FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return {"error": f"Memory {memory_id} not found"}

        old_content = row["content"]
        old_meta = json.loads(row["metadata"]) if row["metadata"] else {}

        # Build edit tracking metadata
        edit_meta = new_metadata or {}
        edit_meta["edited_at"] = time.time()
        edit_meta["edit_count"] = old_meta.get("edit_count", 0) + 1

        result = db.update_memory(
            self.conn,
            memory_id=memory_id,
            content=new_content,
            metadata=edit_meta,
        )

        if result is None:
            return {"error": f"Memory {memory_id} not found"}

        result["old_content_preview"] = old_content[:100]
        return result

    def stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        counts = db.count_memories(self.conn)
        return {
            "agent_pubkey": self.identity.public_key_hex,
            **counts,
        }

    def _find_duplicate(
        self, content: str, memory_type: str,
        metadata: dict[str, Any] | None = None, limit: int = 100,
    ) -> dict[str, Any] | None:
        """Check recent memories of the same type for near-duplicates.

        Uses Jaccard word similarity on content. For transaction memories,
        also requires matching vendor and amount_sats to prevent false positives
        on "Paid X sats to Y" patterns with different amounts.

        Returns the existing memory dict if a duplicate is found, else None.
        """
        threshold = _DEDUP_THRESHOLDS.get(memory_type, _DEDUP_DEFAULT_THRESHOLD)

        rows = self.conn.execute(
            """SELECT id, content, type, metadata, created_at
               FROM memories WHERE type = ?
               ORDER BY created_at DESC LIMIT ?""",
            (memory_type, limit),
        ).fetchall()

        for row in rows:
            similarity = _jaccard(content, row["content"])
            if similarity >= threshold:
                row_meta = json.loads(row["metadata"]) if row["metadata"] else {}

                # For transactions, require matching vendor + amount
                if memory_type == "transaction" and metadata:
                    new_vendor = metadata.get("vendor", "")
                    new_amount = metadata.get("amount_sats")
                    old_vendor = row_meta.get("vendor", "")
                    old_amount = row_meta.get("amount_sats")
                    if new_vendor and old_vendor:
                        if normalize_vendor(new_vendor) != normalize_vendor(old_vendor):
                            continue
                    if new_amount is not None and old_amount is not None:
                        if int(new_amount) != int(old_amount):
                            continue

                return {
                    "id": row["id"],
                    "content": row["content"],
                    "type": row["type"],
                    "metadata": row_meta,
                    "created_at": row["created_at"],
                }
        return None

    def _detect_contradictions(
        self,
        content: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Detect potential contradictions with existing memories.

        For transaction/vendor memories with a vendor in metadata, checks
        for conflicting information: different prices for same service,
        contradictory reliability assessments, etc.

        Returns a list of contradiction dicts with the conflicting memory
        and a description of the conflict. Empty list if no contradictions.
        """
        if not metadata or not metadata.get("vendor"):
            return []

        vendor_norm = normalize_vendor(metadata["vendor"])
        contradictions: list[dict[str, Any]] = []

        # Only check relevant types
        check_types = ("transaction", "vendor", "decision")
        if memory_type not in check_types:
            return []

        rows = self.conn.execute(
            """SELECT id, content, type, metadata, created_at
               FROM memories WHERE type IN ('transaction', 'vendor', 'decision')
               ORDER BY created_at DESC LIMIT 200""",
        ).fetchall()

        content_lower = content.lower()
        new_amount = metadata.get("amount_sats")

        # Sentiment indicators
        positive_words = {"reliable", "fast", "good", "great", "excellent", "trustworthy", "recommended"}
        negative_words = {"unreliable", "slow", "bad", "scam", "avoid", "terrible", "failed", "overpriced"}

        new_positive = any(w in content_lower for w in positive_words)
        new_negative = any(w in content_lower for w in negative_words)

        for row in rows:
            row_meta = json.loads(row["metadata"]) if row["metadata"] else {}
            row_vendor = normalize_vendor(row_meta["vendor"]) if row_meta.get("vendor") else ""

            if row_vendor != vendor_norm:
                continue

            row_content_lower = row["content"].lower()
            old_amount = row_meta.get("amount_sats")

            # Price contradiction: same vendor, significantly different price for same type of service
            if (new_amount is not None and old_amount is not None
                    and memory_type == "transaction" and row["type"] == "transaction"):
                new_amt = int(new_amount)
                old_amt = int(old_amount)
                if old_amt > 0 and new_amt > 0:
                    ratio = max(new_amt, old_amt) / min(new_amt, old_amt)
                    if ratio >= 3.0:
                        contradictions.append({
                            "type": "price_change",
                            "existing_id": row["id"],
                            "existing_preview": row["content"][:100],
                            "existing_created_at": db.format_utc(row["created_at"]),
                            "detail": f"Price changed {ratio:.1f}x: was {old_amt} sats, now {new_amt} sats",
                        })

            # Sentiment contradiction: positive vs negative about same vendor
            old_positive = any(w in row_content_lower for w in positive_words)
            old_negative = any(w in row_content_lower for w in negative_words)

            if new_positive and old_negative:
                contradictions.append({
                    "type": "sentiment_conflict",
                    "existing_id": row["id"],
                    "existing_preview": row["content"][:100],
                    "existing_created_at": db.format_utc(row["created_at"]),
                    "detail": f"New memory is positive but existing memory is negative about {vendor_norm}",
                })
            elif new_negative and old_positive:
                contradictions.append({
                    "type": "sentiment_conflict",
                    "existing_id": row["id"],
                    "existing_preview": row["content"][:100],
                    "existing_created_at": db.format_utc(row["created_at"]),
                    "detail": f"New memory is negative but existing memory is positive about {vendor_norm}",
                })

        # Limit to top 3 most relevant contradictions
        return contradictions[:3]

    def _generate_id(self, content: str) -> str:
        """Generate a deterministic ID from content + timestamp."""
        raw = f"{content}:{time.time()}:{self.identity.public_key_hex}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _parse_since(self, since: str) -> float:
        """Parse a relative time string like '1h', '24h', '7d' into a Unix timestamp."""
        return parse_since(since)

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
