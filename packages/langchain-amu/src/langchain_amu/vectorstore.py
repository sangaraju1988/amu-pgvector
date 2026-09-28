"""AMUVectorStore: a LangChain VectorStore backed by amu-pgvector.

Every read (similarity_search, similarity_search_by_vector,
max_marginal_relevance_search, get_by_ids) runs SQL under the AMUStore's
configured DSN -- its own Postgres role -- so Postgres RLS gates retrieval
exactly the same way here as it does over raw SQL or through the MCP
server. There is no separate access-control path for LangChain users to
fall out of sync with.

Documents map to AMUs: page_content is the description, metadata carries
metric_name, owner_department, definition_hash and value.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
from amu_pgvector import AMUStore
from amu_pgvector.models import SearchResult
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_core.vectorstores.utils import maximal_marginal_relevance


def _to_document(result: SearchResult) -> Document:
    value = result.value if isinstance(result.value, dict) else {"value": result.value}
    user_metadata = value.get("_metadata", {})
    return Document(
        page_content=result.description,
        metadata={
            **user_metadata,
            "metric_name": result.metric_name,
            "owner_department": result.owner_department,
            "definition_hash": result.definition_hash,
            "value": value.get("_amu_value", value),
        },
        id=result.external_id or str(result.id),
    )


class AMUVectorStore(VectorStore):
    def __init__(
        self,
        store: AMUStore,
        embedding: Embeddings,
        *,
        default_owner_department: str = "default",
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._default_owner_department = default_owner_department

    @property
    def embeddings(self) -> Embeddings | None:
        return self._embedding

    def _embed_fn(self, text: str) -> list[float]:
        return self._embedding.embed_query(text)

    # -- write path -------------------------------------------------------

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Every AMU needs a metric_name/owner_department/value even for a
        generic "just add this text" call with no lineage-bearing metadata
        -- metadata may supply them (plus an optional `sql` the text was
        derived from, for real lineage-gated writes); anything left
        unspecified defaults to an ungated, always-visible AMU (empty
        lineage: nothing sensitive was touched, because nothing was
        derived from a query at all)."""
        texts = list(texts)
        metadatas = list(metadatas) if metadatas is not None else [{} for _ in texts]
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts]

        out_ids: list[str] = []
        for text, metadata, ext_id in zip(texts, metadatas, ids, strict=True):
            metadata = dict(metadata or {})
            metric_name = metadata.pop("metric_name", None) or f"doc-{ext_id}"
            owner_department = metadata.pop("owner_department", self._default_owner_department)
            amu_value = metadata.pop("value", {"text": text})
            sql = metadata.pop("sql", "")
            # Everything else in the caller's metadata is preserved verbatim
            # (round-tripped back out in Document.metadata alongside the
            # governance fields on read) rather than discarded.
            stored_value = {"_amu_value": amu_value, "_metadata": metadata}
            self._store.record(
                sql,
                stored_value,
                metric_name=metric_name,
                description=text,
                owner_department=owner_department,
                embed_fn=self._embed_fn,
                external_id=ext_id,
            )
            out_ids.append(ext_id)
        return out_ids

    def delete(self, ids: list[str] | None = None, **kwargs: Any) -> bool | None:
        if not ids:
            return True
        self._store.delete_by_external_ids(ids)
        return True

    def get_by_ids(self, ids: Sequence[str]) -> list[Document]:
        results = self._store.get_by_external_ids(list(ids))
        by_id = {r.external_id: r for r in results}
        return [_to_document(by_id[i]) for i in ids if i in by_id]

    # -- read path (RLS applies under the store's own DSN/role) -----------

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        results = self._store.search(
            query, k, embed_fn=self._embed_fn, metric_name=kwargs.get("metric_name")
        )
        return [_to_document(r) for r in results]

    def similarity_search_by_vector(
        self, embedding: list[float], k: int = 4, **kwargs: Any
    ) -> list[Document]:
        results = self._store.search(
            "", k, embed_fn=lambda _: embedding, metric_name=kwargs.get("metric_name")
        )
        return [_to_document(r) for r in results]

    def similarity_search_with_score(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[tuple[Document, float]]:
        results = self._store.search(
            query, k, embed_fn=self._embed_fn, metric_name=kwargs.get("metric_name")
        )
        return [(_to_document(r), r.distance) for r in results]

    def max_marginal_relevance_search(
        self,
        query: str,
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        **kwargs: Any,
    ) -> list[Document]:
        return self.max_marginal_relevance_search_by_vector(
            self._embed_fn(query), k, fetch_k, lambda_mult, **kwargs
        )

    def max_marginal_relevance_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        **kwargs: Any,
    ) -> list[Document]:
        candidates = self._store.search_with_vectors(
            embedding, fetch_k, metric_name=kwargs.get("metric_name")
        )
        if not candidates:
            return []
        selected = maximal_marginal_relevance(
            np.array(embedding),
            [vec for _, vec in candidates],
            lambda_mult=lambda_mult,
            k=k,
        )
        return [_to_document(candidates[i][0]) for i in selected]

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        dsn: str | None = None,
        store: AMUStore | None = None,
        **kwargs: Any,
    ) -> AMUVectorStore:
        if store is None:
            if dsn is None:
                raise ValueError("AMUVectorStore.from_texts requires dsn= or store=")
            store = AMUStore(dsn)
        vectorstore = cls(store, embedding, **kwargs)
        vectorstore.add_texts(texts, metadatas, ids=ids)
        return vectorstore
