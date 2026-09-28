# amu-pgvector

Python client for **amu-pgvector**: a reference implementation of
Lineage-Aware Memory Governance (Sangaraju & Vissa, *IEEE Access*,
[10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363))
on PostgreSQL + [pgvector](https://github.com/pgvector/pgvector).

An agent may reuse a cached analytical result only if every sensitive
column touched by that result's derivation is in the requester's
permitted set: `S(a) ⊆ P(d)`. Postgres enforces this itself through row-
level security — it is not a filter this client has to remember to add.

Full project docs, the SQL schema, benchmarks, and the LangChain/MCP
integrations live in the main repository:
**https://github.com/sangaraju1988/amu-pgvector**

## Install

```bash
pip install amu-pgvector
```

Extras:

```bash
pip install amu-pgvector[mcp]   # adds the amu-pgvector-mcp MCP server console script
pip install amu-pgvector[st]    # adds sentence-transformers for real embeddings
```

This client talks to a Postgres database that already has the schema
installed — see
[`sql/amu_pgvector.sql`](https://github.com/sangaraju1988/amu-pgvector/blob/main/sql/amu_pgvector.sql)
and the
[60-second quickstart](https://github.com/sangaraju1988/amu-pgvector#60-second-quickstart)
in the main README for the `docker compose up` + `psql -f` steps.

## Quickstart

```python
from amu_pgvector import AMUStore
from amu_pgvector.embeddings import fake_embedder

DSN = "postgresql://amu_owner:amu_owner_password@localhost:5433/amu_dev"
embed = fake_embedder(dim=1536)  # swap for a real embedding model in production

admin = AMUStore(DSN)
admin.register_sensitive_column("income")
admin.grant_department_permission("Finance", "income")
admin.create_agent_role("finance_agent", "Finance", "finance_pw")
admin.create_agent_role("marketing_agent", "Marketing", "marketing_pw")

admin.record(
    "SELECT avg(income) FROM customers",
    {"avg": 82000},
    metric_name="avg_income",
    description="average customer income",
    owner_department="Finance",
    embed_fn=embed,
)

finance_dsn = "postgresql://finance_agent:finance_pw@localhost:5433/amu_dev"
marketing_dsn = "postgresql://marketing_agent:marketing_pw@localhost:5433/amu_dev"

AMUStore(finance_dsn).search("average customer income", k=5, embed_fn=embed)
# -> [SearchResult(metric_name='avg_income', ...)]

AMUStore(marketing_dsn).search("average customer income", k=5, embed_fn=embed)
# -> [] -- Marketing was never granted `income`, so Postgres itself
#          never returns the row, regardless of how the query is asked.
```

## What's in this package

- **`AMUStore`** -- the client. `record()` extracts lineage from the SQL
  that actually produced a cached result (via
  [amu-governance](https://pypi.org/project/amu-governance/)'s
  `sql_lineage`, not self-reported by an agent), computes its
  `definition_hash`, checks for conflicting definitions, and inserts.
  `search()` runs entirely under the caller's own Postgres role, so row-
  level security gates it the same way it gates raw SQL. Admin helpers
  (`register_sensitive_column`, `grant_department_permission`,
  `create_agent_role`, `register_materialization_edge`) manage the
  governance policy.
- **`amu_pgvector.embeddings`** -- `fake_embedder()` (deterministic, no
  network or model download) and `sentence_transformer_embedder()`
  (needs the `[st]` extra).
- **`amu-pgvector-mcp`** (the `[mcp]` extra) -- an MCP server exposing
  `amu_search`, `amu_record`, and `amu_check_conflict` as lineage-gated
  tools, listed on the
  [MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.sangaraju1988/amu-pgvector)
  as `io.github.sangaraju1988/amu-pgvector`.

For the LangChain integration (`AMUVectorStore`, `AMURetriever`), see
[langchain-amu](https://pypi.org/project/langchain-amu/).

## Links

- Main repo (SQL schema, quickstart, benchmarks, threat model): https://github.com/sangaraju1988/amu-pgvector
- Paper: Sangaraju & Vissa, *IEEE Access*, DOI [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363)
- Reference lineage library: [amu-governance](https://pypi.org/project/amu-governance/)
- License: MIT

<!-- mcp-name: io.github.sangaraju1988/amu-pgvector -->
