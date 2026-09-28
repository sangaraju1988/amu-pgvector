"""amu-pgvector: Lineage-Aware Memory Governance on PostgreSQL + pgvector.

Postgres row-level security enforces S(a) subset P(d) at the row level, so
that reuse of a cached analytical result (an Analytical Memory Unit, AMU) is
gated on its derivation lineage. See sql/amu_pgvector.sql for the enforced
schema and docs/design.md for how each paper concept maps to a SQL object.

Implementation to follow in Phase 3 (Python client).
"""

__version__ = "0.1.0"
