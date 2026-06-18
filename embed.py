"""Local embeddings via fastembed (BAAI/bge-small-en-v1.5, 384-dim, ONNX, CPU).

No API key — fits the all-free ethos. Model is lazy-loaded once. Used for the
prior_art_search query embedding and to embed patent abstracts at ingest.
"""
from __future__ import annotations

import logging
from typing import Optional

import config

logger = logging.getLogger("patent.embed")

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # heavy import; deferred
        logger.info(f"loading embedding model {config.EMBED_MODEL}…")
        _model = TextEmbedding(model_name=config.EMBED_MODEL)
    return _model


def embed_one(text: str) -> Optional[list]:
    if not text or not text.strip():
        return None
    try:
        m = _get_model()
        vec = next(iter(m.embed([text])))
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"embed_one failed: {e}")
        return None


def embed_many(texts: list) -> list:
    """Returns a list aligned with `texts`; None for blanks/failures."""
    idx = [i for i, t in enumerate(texts) if t and str(t).strip()]
    out: list = [None] * len(texts)
    if not idx:
        return out
    try:
        m = _get_model()
        vecs = list(m.embed([texts[i] for i in idx]))
        for j, i in enumerate(idx):
            out[i] = [float(x) for x in vecs[j]]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"embed_many failed: {e}")
    return out


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False
