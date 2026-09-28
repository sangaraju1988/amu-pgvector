"""AMUStore: the Python client for amu-pgvector.

A thin wrapper around one Postgres DSN. Which methods actually work depends
entirely on what Postgres privileges that DSN's role has -- record() needs
an amu_writer-member role, search() just needs amu_agent_base, and the
admin_* helpers need the schema owner. Nothing here re-implements or
weakens the gate: every security-relevant decision (S(a) subset P(d),
lineage closure, sensitivity tagging) happens in sql/amu_pgvector.sql, not
in this client.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import psycopg
from amu_governance.sql_lineage import lineage_from_sql
from pgvector.psycopg import register_vector
from psycopg.types.json import Json

from amu_pgvector.models import Conflict, RecordResult, SearchResult

EmbedFn = Callable[[str], Sequence[float]]


def _lineage_json(lineage) -> dict:
    return {
        "steps": [
            {"table": step.table, "columns_used": list(step.columns_used)}
            for step in lineage.steps
        ],
        "filter_logic": lineage.filter_logic,
    }


class AMUStore:
    def __init__(self, dsn: str, *, sql_dialect: str = "postgres") -> None:
        self._dsn = dsn
        self._sql_dialect = sql_dialect

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn)
        register_vector(conn)
        return conn

    # -- writer path -----------------------------------------------------

    def record(
        self,
        sql: str,
        result,
        *,
        metric_name: str,
        description: str,
        owner_department: str,
        embed_fn: EmbedFn,
        epoch: int | None = None,
    ) -> RecordResult:
        """Extract lineage from the executed `sql` (ground truth, not
        self-reported by an agent), compute its definition_hash, check for
        conflicting definitions of the same metric, insert the AMU, and
        return the new id plus lineage_status plus any conflicts found.

        `result` is the analytical value being cached (must be JSON-
        serializable); `description` is the natural-language text that gets
        embedded for retrieval.
        """
        lineage = lineage_from_sql(sql, dialect=self._sql_dialect)
        definition_hash = lineage.definition_hash()
        embedding = list(embed_fn(description))
        if epoch is None:
            epoch = int(time.time())

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT conflicting_department, other_hash FROM amu.check_conflict(%s, %s, %s)",
                (metric_name, definition_hash, owner_department),
            )
            conflicts = [Conflict(dept, other_hash) for dept, other_hash in cur.fetchall()]

            cur.execute(
                """
                INSERT INTO amu.memory_units
                    (metric_name, description, value, owner_department, epoch,
                     lineage, definition_hash, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, lineage_status
                """,
                (
                    metric_name,
                    description,
                    Json(result),
                    owner_department,
                    epoch,
                    Json(_lineage_json(lineage)),
                    definition_hash,
                    embedding,
                ),
            )
            amu_id, lineage_status = cur.fetchone()
            conn.commit()

        return RecordResult(amu_id=amu_id, lineage_status=lineage_status, conflicts=conflicts)

    # -- agent path (RLS applies under this DSN's own role) --------------

    def search(
        self,
        query: str,
        k: int,
        embed_fn: EmbedFn,
        *,
        metric_name: str | None = None,
    ) -> list[SearchResult]:
        embedding = list(embed_fn(query))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, metric_name, description, value, owner_department, "
                "definition_hash, distance "
                "FROM amu.search(%s::vector, %s, %s)",
                (embedding, k, metric_name),
            )
            return [
                SearchResult(
                    id=row[0],
                    metric_name=row[1],
                    description=row[2],
                    value=row[3],
                    owner_department=row[4],
                    definition_hash=row[5],
                    distance=row[6],
                )
                for row in cur.fetchall()
            ]

    # -- admin helpers (need schema-owner privilege) ----------------------

    def register_sensitive_column(self, column_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO amu.sensitive_columns (column_id) VALUES (%s) "
                "ON CONFLICT DO NOTHING",
                (column_id,),
            )
            conn.commit()

    def grant_department_permission(self, department: str, column_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO amu.department_permissions (department, column_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (department, column_id),
            )
            conn.commit()

    def create_agent_role(self, role_name: str, department: str, password: str) -> None:
        """Create a LOGIN role that's a member of amu_agent_base and mapped
        to `department` in amu.role_departments -- the identity mechanism
        strong mode relies on (session_user, not anything the client sets).
        """
        from psycopg import sql as pg_sql

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_agent_base").format(
                    pg_sql.Identifier(role_name), pg_sql.Literal(password)
                )
            )
            cur.execute(
                "INSERT INTO amu.role_departments (role_name, department) VALUES (%s, %s)",
                (role_name, department),
            )
            conn.commit()

    def register_materialization_edge(
        self, derived_table: str, source_table: str, column_map: dict[str, str | list[str]]
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO amu.materialization_edges "
                "(derived_table, source_table, column_map) VALUES (%s, %s, %s) "
                "ON CONFLICT (derived_table, source_table) DO UPDATE SET column_map = EXCLUDED.column_map",
                (derived_table, source_table, Json(column_map)),
            )
            conn.commit()
