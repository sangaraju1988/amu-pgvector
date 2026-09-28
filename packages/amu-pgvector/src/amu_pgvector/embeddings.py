"""Pluggable embedding functions.

An embed_fn is just Callable[[str], Sequence[float]]. `fake_embedder` is
deterministic and needs no network or model download -- it's what the test
suite and the quickstart use by default. `sentence_transformer_embedder`
needs the optional `[st]` extra; the import is deferred so the base package
never requires sentence-transformers/torch.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable


def fake_embedder(dim: int = 1536) -> Callable[[str], list[float]]:
    """A deterministic, hash-based embedder: same text always maps to the
    same vector, semantically meaningless but stable and dependency-free.
    Two different strings produce different vectors; that's all the test
    suite (and a quickstart with nothing else installed) needs."""

    def _embed(text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < dim:
            digest = hashlib.sha256(f"{text}\0{counter}".encode()).digest()
            for i in range(0, len(digest) - 3, 4):
                if len(values) >= dim:
                    break
                (as_uint,) = struct.unpack(">I", digest[i : i + 4])
                # Map to [-1, 1].
                values.append((as_uint / 0xFFFFFFFF) * 2 - 1)
            counter += 1
        return values

    return _embed


def sentence_transformer_embedder(
    model_name: str = "all-MiniLM-L6-v2",
) -> Callable[[str], list[float]]:
    """Requires the `[st]` extra (sentence-transformers). Real semantic
    embeddings, for examples and production use -- not for the test suite,
    which must never need a model download."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - exercised only without [st]
        raise ImportError(
            "sentence_transformer_embedder requires the 'st' extra: "
            "pip install amu-pgvector[st]"
        ) from exc

    model = SentenceTransformer(model_name)

    def _embed(text: str) -> list[float]:
        return model.encode(text, normalize_embeddings=True).tolist()

    return _embed


__all__ = ["fake_embedder", "sentence_transformer_embedder"]
