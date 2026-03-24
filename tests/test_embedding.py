"""Tests for embedding engine and semantic search integration."""

import math
import struct
import pytest

from lightning_memory.embedding import (
    cosine_similarity,
    generate_embedding,
    has_embeddings,
    _hash_embedding,
    reset_state,
)
from lightning_memory.db import get_connection, store_embedding, query_by_embedding, store_memory
from lightning_memory.memory import MemoryEngine
from lightning_memory.nostr import NostrIdentity


# --- cosine_similarity unit tests ---


def test_cosine_identical():
    v = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


def test_cosine_zero_vector():
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0


# --- hash_embedding fallback tests ---


def test_hash_embedding_deterministic():
    a = _hash_embedding("hello world")
    b = _hash_embedding("hello world")
    assert a == b


def test_hash_embedding_dimension():
    v = _hash_embedding("test", dimension=384)
    assert len(v) == 384


def test_hash_embedding_normalized():
    v = _hash_embedding("test")
    magnitude = math.sqrt(sum(x * x for x in v))
    assert abs(magnitude - 1.0) < 1e-6


def test_hash_different_inputs():
    a = _hash_embedding("vendor A is great")
    b = _hash_embedding("vendor B is terrible")
    # Different inputs should produce different embeddings
    assert a != b


# --- generate_embedding tests (works with or without onnxruntime) ---


def test_generate_embedding_returns_384():
    reset_state()
    v = generate_embedding("test query")
    assert len(v) == 384


def test_generate_embedding_cached():
    reset_state()
    v1 = generate_embedding("cache test")
    v2 = generate_embedding("cache test")
    assert v1 is v2  # same object from cache


# --- DB embedding storage tests ---


def test_store_and_query_embedding():
    conn = get_connection(":memory:")
    # Store a memory first
    store_memory(conn, "mem1", "Paid 500 sats to bitrefill for gift card", "transaction")
    store_memory(conn, "mem2", "OpenAI returned 429 rate limit error", "error")

    # Store embeddings
    vec1 = _hash_embedding("bitrefill payment gift card")
    vec2 = _hash_embedding("openai rate limit error")
    store_embedding(conn, "mem1", vec1)
    store_embedding(conn, "mem2", vec2)

    # Query with similar embedding
    query_vec = _hash_embedding("bitrefill payment gift card")
    results = query_by_embedding(conn, query_vec, limit=5)

    assert len(results) == 2
    # First result should be the most similar (same hash = identical vector)
    assert results[0]["id"] == "mem1"
    assert results[0]["similarity"] == 1.0


def test_query_by_embedding_with_type_filter():
    conn = get_connection(":memory:")
    store_memory(conn, "t1", "Paid 500 sats", "transaction")
    store_memory(conn, "e1", "Got error 500", "error")

    vec1 = _hash_embedding("payment sats")
    vec2 = _hash_embedding("server error")
    store_embedding(conn, "t1", vec1)
    store_embedding(conn, "e1", vec2)

    results = query_by_embedding(conn, vec1, limit=5, memory_type="transaction")
    assert len(results) == 1
    assert results[0]["id"] == "t1"


# --- Hybrid search integration ---


def test_memory_query_works_without_embeddings():
    """Query should work fine when embeddings are not installed."""
    conn = get_connection(":memory:")
    identity = NostrIdentity.generate()
    engine = MemoryEngine(conn=conn, identity=identity)

    engine.store("Paid 500 sats to bitrefill for gift card", memory_type="transaction")
    results = engine.query("bitrefill")
    assert len(results) >= 1


def test_merge_results_dedup():
    """Merged results should not contain duplicates."""
    fts = [
        {"id": "a", "content": "x", "relevance": 1.0},
        {"id": "b", "content": "y", "relevance": 0.8},
    ]
    sem = [
        {"id": "b", "content": "y", "similarity": 0.9},
        {"id": "c", "content": "z", "similarity": 0.7},
    ]
    merged = MemoryEngine._merge_results(fts, sem, limit=10)
    ids = [r["id"] for r in merged]
    assert len(ids) == len(set(ids))  # no duplicates
    assert set(ids) == {"a", "b", "c"}
