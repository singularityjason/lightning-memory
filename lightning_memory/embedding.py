"""Optional embedding engine for semantic search.

Provides 384-dim normalized vectors via ONNX Runtime (all-MiniLM-L6-v2).
Falls back to hash-based pseudo-embeddings when onnxruntime is not installed.

Install: pip install lightning-memory[semantic]
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("lightning_memory.embedding")

# Model state
_MODEL: tuple | None = None  # (tokenizer, onnx_session)
_BACKEND: str | None = None  # "onnx" or None
_LOAD_ATTEMPTED = False
_MODEL_DIR = Path.home() / ".cache" / "lightning-memory" / "models" / "all-MiniLM-L6-v2-onnx"

# LRU cache for repeated queries
_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_CACHE_MAX = 256

# NumPy (required for ONNX path)
try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]


def has_embeddings() -> bool:
    """Check if semantic embeddings are available (onnxruntime installed)."""
    try:
        import importlib.util
        return importlib.util.find_spec("onnxruntime") is not None
    except Exception:
        return False


def get_embedding_info() -> dict[str, Any]:
    """Return current embedding model info."""
    return {
        "model": "all-MiniLM-L6-v2",
        "dimension": 384,
        "backend": _BACKEND,
        "model_loaded": _MODEL is not None,
        "available": has_embeddings(),
    }


def reset_state() -> None:
    """Reset all embedding state (for testing)."""
    global _MODEL, _BACKEND, _LOAD_ATTEMPTED
    _MODEL = None
    _BACKEND = None
    _LOAD_ATTEMPTED = False
    _CACHE.clear()


def _get_model() -> tuple | None:
    """Lazy-load the ONNX model. Returns (tokenizer, session) or None."""
    global _MODEL, _BACKEND, _LOAD_ATTEMPTED
    if _MODEL is not None:
        return _MODEL
    if _LOAD_ATTEMPTED:
        return None
    _LOAD_ATTEMPTED = True

    if os.environ.get("LM_SKIP_EMBEDDINGS") == "1":
        return None

    if not has_embeddings():
        return None

    model_dir = _MODEL_DIR
    model_path = model_dir / "model.onnx"
    tokenizer_path = model_dir / "tokenizer.json"

    # Auto-download if not present
    if not model_path.exists():
        if not _download_model(model_dir):
            return None

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer as FastTokenizer

        tokenizer = FastTokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        tokenizer.enable_truncation(max_length=512)

        sess_opts = ort.SessionOptions()
        sess_opts.log_severity_level = 4
        sess_opts.log_verbosity_level = 0
        sess_opts.enable_cpu_mem_arena = False

        session = ort.InferenceSession(
            str(model_path),
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        _MODEL = (tokenizer, session)
        _BACKEND = "onnx"
        logger.info("Loaded ONNX embedding model (all-MiniLM-L6-v2)")
        return _MODEL
    except Exception as e:
        logger.warning("Failed to load ONNX model: %s", e)
        return None


def _download_model(model_dir: Path) -> bool:
    """Download all-MiniLM-L6-v2 ONNX model from HuggingFace."""
    try:
        from huggingface_hub import snapshot_download
        logger.info("Downloading all-MiniLM-L6-v2 ONNX model...")
        snapshot_download(
            repo_id="sentence-transformers/all-MiniLM-L6-v2",
            local_dir=str(model_dir),
            allow_patterns=["model.onnx", "tokenizer.json", "config.json"],
        )
        return (model_dir / "model.onnx").exists()
    except ImportError:
        logger.info(
            "huggingface_hub not installed. To enable semantic search: "
            "pip install lightning-memory[semantic] && "
            "python -c \"from lightning_memory.embedding import _download_model; "
            "from pathlib import Path; _download_model(Path.home() / '.cache/lightning-memory/models/all-MiniLM-L6-v2-onnx')\""
        )
        return False
    except Exception as e:
        logger.warning("Model download failed: %s", e)
        return False


def _onnx_encode(tokenizer: Any, session: Any, texts: list[str]) -> list[list[float]]:
    """Encode texts using ONNX Runtime. Returns normalized embeddings."""
    batch = tokenizer.encode_batch(texts)
    ids = np.array([b.ids for b in batch], dtype=np.int64)
    mask = np.array([b.attention_mask for b in batch], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    input_names = {i.name for i in session.get_inputs()}
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = np.zeros_like(ids)
    outputs = session.run(None, feed)
    embeddings = outputs[1] if len(outputs) > 1 else outputs[0]
    if embeddings.ndim == 3:
        # Mean pooling
        mask_expanded = mask[:, :, np.newaxis].astype(np.float32)
        sum_emb = np.sum(embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
        embeddings = sum_emb / sum_mask
    # L2 normalize
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.clip(norms, a_min=1e-9, a_max=None)
    return [row.tolist() for row in normalized]


def _hash_embedding(text: str, dimension: int = 384) -> list[float]:
    """Fallback: deterministic pseudo-embedding from text hash.

    Not useful for real semantic search, but prevents crashes when
    onnxruntime is not installed.
    """
    hash_digest = hashlib.md5(text.encode()).digest()
    seed = int.from_bytes(hash_digest[:4], byteorder="big")

    import random
    rng = random.Random(seed)
    vector = [rng.gauss(0, 1) for _ in range(dimension)]

    magnitude = math.sqrt(sum(x * x for x in vector))
    if magnitude == 0:
        return [1.0 / math.sqrt(dimension)] * dimension
    return [x / magnitude for x in vector]


def generate_embedding(text: str, dimension: int = 384) -> list[float]:
    """Generate a 384-dim normalized embedding from text.

    Uses ONNX Runtime if available, otherwise falls back to hash-based.
    Results are LRU-cached.
    """
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _CACHE:
        _CACHE.move_to_end(cache_key)
        return _CACHE[cache_key]

    model = _get_model()
    if model is not None:
        tokenizer, session = model
        results = _onnx_encode(tokenizer, session, [text])
        result = results[0]
    else:
        result = _hash_embedding(text, dimension)

    _CACHE[cache_key] = result
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
