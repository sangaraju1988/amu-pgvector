# langchain-amu

LangChain integration for **amu-pgvector**: a `VectorStore` and
`Retriever` backed by lineage-gated Analytical Memory Units on
PostgreSQL + pgvector, part of a reference implementation of
Lineage-Aware Memory Governance (Sangaraju & Vissa, *IEEE Access*,
[10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363)).

Every read runs under the store's own Postgres role, so row-level
security gates LangChain retrieval exactly the same way it gates raw SQL
or the MCP server — there is no separate access-control path to keep in
sync.

Full project docs, the SQL schema, and benchmarks live in the main
repository: **https://github.com/sangaraju1988/amu-pgvector**

## Install

```bash
pip install langchain-amu
```

This depends on and reuses [amu-pgvector](https://pypi.org/project/amu-pgvector/)'s
client. You'll also need the schema installed in Postgres — see the
[60-second quickstart](https://github.com/sangaraju1988/amu-pgvector#60-second-quickstart)
in the main README.

## Quickstart

```python
from amu_pgvector import AMUStore
from langchain_amu import AMUVectorStore, AMURetriever
from langchain_core.embeddings import DeterministicFakeEmbedding

store = AMUStore("postgresql://finance_agent:finance_pw@localhost:5433/amu_dev")
embedding = DeterministicFakeEmbedding(size=1536)  # swap for a real embeddings model

vectorstore = AMUVectorStore(store, embedding)
vectorstore.similarity_search("average customer income", k=5)

retriever = AMURetriever(vectorstore=vectorstore, k=5)
retriever.invoke("average customer income")
```

A restricted role (one never granted the sensitive column a cached
result was derived from) gets nothing back from `similarity_search`,
`max_marginal_relevance_search`, or the retriever — enforced by Postgres,
not by this library.

## What's in this package

- **`AMUVectorStore(VectorStore)`** -- `similarity_search`,
  `similarity_search_by_vector`, `max_marginal_relevance_search`,
  `add_texts`/`add_documents` (with `ids=[...]` upsert), `delete`,
  `get_by_ids`. `Document.metadata` carries `metric_name`,
  `owner_department`, `definition_hash`, and `value` alongside whatever
  metadata the caller supplied.
- **`AMURetriever(BaseRetriever)`** -- wraps an `AMUVectorStore`.

Tested against LangChain's own standard `VectorStoreIntegrationTests`
suite; see the main repo's
[`docs/design.md`](https://github.com/sangaraju1988/amu-pgvector/blob/main/docs/design.md)
for the handful of tests that don't apply here and why (every AMU
necessarily exposes governance fields in `Document.metadata`, which
conflicts with a few of the suite's generic exact-metadata-echo
assertions — content, ids, add/delete/mutate/search all genuinely work).

## Links

- Main repo (SQL schema, quickstart, benchmarks, threat model): https://github.com/sangaraju1988/amu-pgvector
- Paper: Sangaraju & Vissa, *IEEE Access*, DOI [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363)
- Python client: [amu-pgvector](https://pypi.org/project/amu-pgvector/)
- License: MIT
