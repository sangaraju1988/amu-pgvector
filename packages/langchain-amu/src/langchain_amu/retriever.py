"""AMURetriever: a LangChain BaseRetriever over AMUVectorStore.

Retrieval goes through AMUVectorStore.similarity_search, so it's gated by
Postgres RLS the same way as everything else in this package -- there's no
separate filtering logic here to keep in sync with the SQL policy.
"""

from __future__ import annotations

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from langchain_amu.vectorstore import AMUVectorStore


class AMURetriever(BaseRetriever):
    vectorstore: AMUVectorStore
    k: int = 4

    model_config = {"arbitrary_types_allowed": True}

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        return self.vectorstore.similarity_search(query, k=self.k)
