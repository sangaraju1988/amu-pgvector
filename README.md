# amu-pgvector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23025849.svg)](https://doi.org/10.5281/zenodo.23025849)

Reference implementation of **Lineage-Aware Memory Governance**
(Sangaraju & Vissa, *IEEE Access*, [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363))
on PostgreSQL + [pgvector](https://github.com/pgvector/pgvector).

## The problem

Say Finance computes `avg(income)` for a customer segment and caches the
result so the next agent that asks the same question doesn't have to
recompute it. Later, a Marketing agent asks a semantically similar
question -- "what's the average customer income by segment?" -- and a
naive shared memory, gated only on the metric's name or content tags,
serves back Finance's cached result. Marketing was never permitted to see
`income`. The result itself doesn't *look* like income -- it's a number --
but it was **derived from** a column Marketing isn't permitted to touch,
and nothing about matching on metric name or content catches that. That's
the leak this project closes: gating reuse on *how a result was derived*,
not on what it looks like.

## 60-second quickstart

```bash
git clone https://github.com/sangaraju1988/amu-pgvector.git
cd amu-pgvector
docker compose up -d
psql -h localhost -p 5433 -U amu_owner -d amu_dev -f sql/amu_pgvector.sql
# password: amu_owner_password
```

```bash
uv sync --all-packages --all-extras   # or: pip install amu-pgvector
```

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

Every line above was run against this exact repo before being written
here (see `docs/design.md` for what a from-scratch install actually looks
like). `amu_owner`/`amu_owner_password` is the docker-compose default --
change it for anything beyond local development.

## How the gate works

`amu.memory_units` is a normal Postgres table with `FORCE ROW LEVEL
SECURITY` enabled. Every write goes through a trigger that extracts the
lineage of the SQL that actually produced the cached result (via
[amu-governance](https://github.com/sangaraju1988/amu-governance)'s
`sql_lineage` module -- not self-reported by the agent), closes it over any
registered materialized-view/derived-table edges, and computes S(a): the
set of sensitive columns that lineage actually touches. A `SELECT` policy
enforces `S(a) ⊆ P(d)` -- the row is visible only if every column in S(a)
is in the requesting role's permitted set P(d) -- for every query against
the table, from every client: raw SQL, `amu.search()`, the Python client,
the LangChain integration, and the MCP server all go through the same
policy, because it's the table's policy, not any one client's filter.

```mermaid
sequenceDiagram
    participant Writer as amu_writer role<br/>(trusted interception layer)
    participant PG as Postgres: amu.memory_units<br/>(FORCE ROW LEVEL SECURITY)
    participant Trigger as BEFORE INSERT trigger
    participant Finance as finance_agent role
    participant Marketing as marketing_agent role

    Writer->>PG: INSERT ... lineage = {"steps":[{"table":"customers","columns_used":["income"]}]}
    PG->>Trigger: compute lineage_columns, S(a), lineage_status
    Trigger-->>PG: sensitive_columns = {income}, resolved

    Finance->>PG: SELECT ... (session_user = finance_agent)
    PG->>PG: S(a)={income} subset P(Finance)={income,...}? yes
    PG-->>Finance: row returned

    Marketing->>PG: SELECT ... (session_user = marketing_agent)
    PG->>PG: S(a)={income} subset P(Marketing)={...}? no
    PG-->>Marketing: row not returned
```

See [`docs/design.md`](docs/design.md) for how every paper concept maps to
a specific SQL object, and [`docs/sql-reference.md`](docs/sql-reference.md)
for an exhaustive reference of every table, function, role and policy.

## Security model and limits

**Protected:** cross-department leakage of a cached result's derivation
columns (the core guarantee, enforced by RLS); silent metric-definition
conflicts (`amu.check_conflict()`); a column reclassified as sensitive
*after* AMUs already exist (a trigger recomputes affected rows, no
rewrite needed); lineage closure that silently truncates a long or cyclic
derivation chain (fails closed -- marks the AMU unresolved and invisible,
rather than serving a partially-traced result).

**Explicitly out of scope:** a writer that lies about lineage (the SQL
gate trusts `amu_writer`'s input by design); inference by combining
several individually-permitted AMUs; Postgres superusers, who always
bypass RLS; timing side channels; a "convenience mode" identity option
(off by default) that's only as safe as the boundary between trusted
application code and agent-influenced code on a pooled connection.

Full writeup: [`docs/threat-model.md`](docs/threat-model.md).

## Benchmarks

Every number below is quoted directly from
[`results/SUMMARY.md`](results/SUMMARY.md), generated by
`benchmarks/generate_summary.py` from committed `results/<name>/<timestamp>/`
runs (git SHA, Postgres/pgvector versions, hardware, seed and parameters
in each run's `manifest.json`). Re-run the benchmark scripts yourself to
reproduce or challenge these; nothing here is estimated.

**Leak rate** (2000 requests over 600 synthetic AMUs, Finance/Marketing/Support):

| Condition | Served | Cross-department leaks | Leak rate |
|---|---|---|---|
| Naive (content-gated, no lineage check) | 2000 | 1010 | 50.5% |
| amu-pgvector (RLS-gated) | 990 | 0 | 0.0% |

**Latency** (p50/p95 at k=10; RLS and `hnsw.iterative_scan` each on/off,
same role/queries/data per row):

| n | RLS | iterative_scan | p50 (ms) | p95 (ms) |
|---|---|---|---|---|
| 10,000 | on | on (`amu.search()` default) | 66.8 | 70.2 |
| 10,000 | on | off | 81.9 | 88.2 |
| 10,000 | off | on | 19.5 | 21.8 |
| 10,000 | off | off | 49.2 | 52.3 |
| 100,000 | on | on (`amu.search()` default) | 712.9 | 851.2 |
| 100,000 | on | off | 43.4 | 65.7 |
| 100,000 | off | on | 376.8 | 418.5 |
| 100,000 | off | off | 46.6 | 70.9 |

Read honestly: `hnsw.iterative_scan=relaxed_order` (what `amu.search()`
uses by default, so that filtered results still hit k even when most rows
are hidden) costs real latency at scale -- roughly 16x slower than
iterative-off at 100k rows in this run. That's the price of the recall
guarantee below; if your visible fraction is consistently high, you may
prefer to query without it. 1M-row scale was not attempted: the 100k HNSW
index build alone took about 7 minutes on the machine this was run on
(Docker Desktop on macOS, sharing the host with unrelated workloads) --
recorded honestly rather than extrapolated.

**Filtered recall** (recall@10 of gated HNSW search vs. exact brute-force
over the same role's actually-visible rows, n=20,000):

| Visible fraction | recall@10 (mean) | recall@10 (min) |
|---|---|---|
| 50% | 100.0% | 100.0% |
| 10% | 100.0% | 100.0% |
| 1% | 100.0% | 100.0% |

## Integrations

- **Plain SQL** -- `sql/amu_pgvector.sql` is the artifact that matters most:
  idempotent, installs on a managed Postgres as a normal database owner
  (no superuser), no C extensions beyond `vector`. Works identically on
  Supabase and Neon (see `submissions/supabase-neon-guide.md` for a
  from-scratch tutorial covering both).
- **LangChain** -- `langchain-amu` provides `AMUVectorStore` and
  `AMURetriever`. Every read runs under the store's own Postgres role, so
  RLS gates LangChain the same way it gates raw SQL. See
  `packages/langchain-amu/`.
- **MCP** -- `pip install amu-pgvector[mcp]` adds an `amu-pgvector-mcp`
  console script exposing `amu_search`, `amu_record` and
  `amu_check_conflict` as tools, gated the same way. See
  `packages/amu-pgvector/src/amu_pgvector/mcp_server.py`.

## Citation

```bibtex
@article{sangaraju2026lineage,
  title   = {Lineage-Aware Memory Governance: A Derivation-Gated Framework
             for Privacy-Preserving Column-Level Access Control in
             Enterprise AI Agents},
  author  = {Sangaraju, Venkata and Vissa, Sudhir},
  journal = {IEEE Access},
  year    = {2026},
  doi     = {10.1109/ACCESS.2026.3730363}
}
```

See [`CITATION.cff`](CITATION.cff) to also cite this software directly.

## License

MIT. See [`LICENSE`](LICENSE).

## Related

- Paper: Sangaraju & Vissa, *IEEE Access*, DOI [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363)
- Reference library: [amu-governance](https://github.com/sangaraju1988/amu-governance) ([PyPI](https://pypi.org/project/amu-governance/))
- [`docs/design.md`](docs/design.md) -- paper concepts mapped to SQL objects, plus every real gotcha found while building this
- [`docs/threat-model.md`](docs/threat-model.md) -- what's protected, what's not
- [`docs/sql-reference.md`](docs/sql-reference.md) -- every table, function, role and policy
