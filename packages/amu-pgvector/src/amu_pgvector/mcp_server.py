"""amu-pgvector-mcp: an MCP server exposing lineage-gated AMU search,
record, and conflict-check as tools.

Connects with the DSN of the Postgres role it's launched as (the
AMU_PGVECTOR_DSN env var) -- results are gated by Postgres row-level
security under THAT role, exactly as everywhere else in this project. If
launched as an amu_agent_base-only role, amu_record will fail with a
Postgres permission error rather than silently degrading -- writes need an
amu_writer-member role. This server never re-implements or bypasses the
gate; it just runs SQL as whichever role it was given.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from amu_pgvector import AMUStore
from amu_pgvector.embeddings import fake_embedder

DSN_ENV_VAR = "AMU_PGVECTOR_DSN"
EMBEDDING_DIM_ENV_VAR = "AMU_PGVECTOR_EMBEDDING_DIM"

_GATING_NOTE = (
    "Results are lineage-gated by Postgres row-level security under this "
    "server's own Postgres role: an Analytical Memory Unit (AMU) is only "
    "ever returned if every sensitive column touched by its derivation is "
    "in this role's permitted set. This tool cannot bypass that gate."
)

mcp = MCPServer(
    name="amu-pgvector",
    instructions=(
        "Lineage-gated agent memory on PostgreSQL + pgvector (Lineage-Aware "
        "Memory Governance, IEEE Access DOI 10.1109/ACCESS.2026.3730363). "
        + _GATING_NOTE
    ),
)

_store: AMUStore | None = None
_embed_fn = None


def _get_store() -> AMUStore:
    global _store
    if _store is None:
        dsn = os.environ.get(DSN_ENV_VAR)
        if not dsn:
            raise RuntimeError(
                f"{DSN_ENV_VAR} must be set to the DSN of the Postgres role "
                "this server should run as."
            )
        _store = AMUStore(dsn)
    return _store


def _get_embed_fn():
    global _embed_fn
    if _embed_fn is None:
        try:
            from amu_pgvector.embeddings import sentence_transformer_embedder

            _embed_fn = sentence_transformer_embedder()
        except ImportError:
            dim = int(os.environ.get(EMBEDDING_DIM_ENV_VAR, "1536"))
            _embed_fn = fake_embedder(dim=dim)
    return _embed_fn


@mcp.tool(
    description=(
        "Search lineage-gated Analytical Memory Units (AMUs) by semantic "
        "similarity to a natural-language query. " + _GATING_NOTE
    )
)
def amu_search(query: str, k: int = 5, metric_name: str | None = None) -> list[dict[str, Any]]:
    results = _get_store().search(query, k, embed_fn=_get_embed_fn(), metric_name=metric_name)
    return [
        {
            "id": str(r.id),
            "metric_name": r.metric_name,
            "description": r.description,
            "value": r.value,
            "owner_department": r.owner_department,
            "definition_hash": r.definition_hash,
            "distance": r.distance,
        }
        for r in results
    ]


@mcp.tool(
    description=(
        "Record a new Analytical Memory Unit (AMU). Lineage is extracted "
        "from the executed `sql` (ground truth), never self-reported. "
        "Requires an amu_writer-member Postgres role -- other roles get a "
        "Postgres permission error, not a silent no-op."
    )
)
def amu_record(
    sql: str,
    value: dict[str, Any],
    metric_name: str,
    description: str,
    owner_department: str,
) -> dict[str, Any]:
    result = _get_store().record(
        sql,
        value,
        metric_name=metric_name,
        description=description,
        owner_department=owner_department,
        embed_fn=_get_embed_fn(),
    )
    return {
        "amu_id": str(result.amu_id),
        "lineage_status": result.lineage_status,
        "conflicts": [
            {"department": c.department, "definition_hash": c.definition_hash}
            for c in result.conflicts
        ],
    }


@mcp.tool(
    description=(
        "Check whether another department has already recorded a "
        "conflicting definition of the same metric_name (same name, "
        "different derivation / definition_hash). Returns only department "
        "names and hashes -- never values or lineage -- so this cannot "
        "become a side channel around the gate."
    )
)
def amu_check_conflict(
    metric_name: str, definition_hash: str, owner_department: str
) -> list[dict[str, str]]:
    conflicts = _get_store().check_conflict(metric_name, definition_hash, owner_department)
    return [
        {"department": c.department, "definition_hash": c.definition_hash} for c in conflicts
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
