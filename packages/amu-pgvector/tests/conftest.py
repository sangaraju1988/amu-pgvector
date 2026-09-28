"""Shared fixtures for the amu-pgvector test suite.

Every test runs against a real Postgres (docker-compose locally, a service
container in CI) -- nothing here is mocked. Each test session gets one
throwaway database installed via the actual sql/amu_pgvector.sql, exactly as
a real deployment would run it (through `psql`, not psycopg, since the
script uses psql meta-commands); each test truncates data and drops any
roles it created before the next one runs.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_FILE = REPO_ROOT / "sql" / "amu_pgvector.sql"

ADMIN_DSN = os.environ.get(
    "AMU_TEST_DSN", "postgresql://amu_owner:amu_owner_password@localhost:5433/amu_dev"
)

# Small dimension for fast HNSW index builds in security/correctness tests.
# Benchmarks (Phase 5) use a realistic dimension explicitly.
TEST_EMBEDDING_DIM = 32

DATA_TABLES = (
    "amu.memory_units",
    "amu.materialization_edges",
    "amu.department_permissions",
    "amu.role_departments",
    "amu.sensitive_columns",
)


def _conninfo_dict(dsn: str) -> dict:
    return psycopg.conninfo.conninfo_to_dict(dsn)


def _pg_env(conn_info: dict) -> dict:
    env = dict(os.environ)
    env["PGHOST"] = str(conn_info.get("host", "localhost"))
    env["PGPORT"] = str(conn_info.get("port", 5432))
    env["PGUSER"] = str(conn_info["user"])
    env["PGPASSWORD"] = str(conn_info["password"])
    env["PGDATABASE"] = str(conn_info["dbname"])
    return env


def _psql_path() -> str:
    return shutil.which("psql") or "psql"


def _grant_admin_on_shared_roles(admin_conn_info: dict, owner_role: str) -> None:
    """amu_writer/amu_agent_base are cluster-global roles (like all Postgres
    roles). When more than one ephemeral test database's install script
    runs in the same session, whichever runs first creates them and gets
    auto-granted ADMIN OPTION on them -- every other ephemeral owner just
    finds them already there (skipped by the script's own IF NOT EXISTS
    guard) and would otherwise be unable to grant/revoke membership in them
    (e.g. `CREATE ROLE ... IN ROLE amu_writer`). The superuser admin
    connection can always grant this, regardless of who created them."""
    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        for role in ("amu_writer", "amu_agent_base"):
            cur.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    sql.Identifier(role), sql.Identifier(owner_role)
                )
            )
    admin.close()


@pytest.fixture(scope="session")
def admin_conn_info() -> dict:
    return _conninfo_dict(ADMIN_DSN)


@pytest.fixture(scope="session")
def test_database(admin_conn_info):
    """One throwaway database + non-superuser owner role per test session,
    with sql/amu_pgvector.sql installed exactly as `psql -f` would run it in
    production. This is the same non-superuser-owner path CI exercises."""
    suffix = secrets.token_hex(4)
    db_name = f"amu_test_{suffix}"
    owner_role = f"amu_test_owner_{suffix}"
    owner_password = secrets.token_hex(8)

    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} CREATEROLE").format(
                sql.Identifier(owner_role), sql.Literal(owner_password)
            )
        )
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(db_name), sql.Identifier(owner_role)
            )
        )
    admin.close()

    # Plain pgvector isn't a "trusted" extension on vanilla Postgres, so
    # creating it needs superuser -- exactly like Supabase/Neon, which
    # pre-install it for you so the app owner's `CREATE EXTENSION IF NOT
    # EXISTS vector` in the install script below is a genuine no-op. We
    # reproduce that same pre-provisioned state here rather than running
    # the whole suite as superuser.
    admin_for_db = psycopg.connect(
        **{**admin_conn_info, "dbname": db_name}, autocommit=True
    )
    with admin_for_db.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    admin_for_db.close()

    owner_conn_info = {
        **admin_conn_info,
        "dbname": db_name,
        "user": owner_role,
        "password": owner_password,
    }
    owner_dsn = psycopg.conninfo.make_conninfo(**owner_conn_info)

    subprocess.run(
        [
            _psql_path(),
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"embedding_dim={TEST_EMBEDDING_DIM}",
            "-v",
            "convenience_mode_enabled=off",
            "-f",
            str(SQL_FILE),
        ],
        env=_pg_env(owner_conn_info),
        check=True,
    )

    _grant_admin_on_shared_roles(admin_conn_info, owner_role)

    yield {
        "dsn": owner_dsn,
        "conn_info": owner_conn_info,
        "db_name": db_name,
        "owner_role": owner_role,
    }

    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        # Any t_role_* roles left by the last test are recorded as grantees
        # with owner_role as grantor (it created them via IN ROLE
        # amu_writer/amu_agent_base) -- Postgres won't drop a role that's
        # still a live grantor, so these must go first.
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 't\\_role\\_%' ESCAPE '\\'")
        for (rolname,) in cur.fetchall():
            cur.execute(f'DROP ROLE IF EXISTS "{rolname}"')
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        cur.execute(f'DROP ROLE IF EXISTS "{owner_role}"')
        # amu_writer/amu_agent_base are cluster-global roles (like all
        # Postgres roles) and, deliberately, NOT dropped here: more than one
        # `test_database` instance can be alive at once in the same pytest
        # process (e.g. langchain-amu's tests reuse this fixture from a
        # separate conftest.py registration, which pytest treats as its own
        # independent session-scoped instance) -- whichever one tears down
        # first must not pull these two shared roles out from under a
        # sibling instance's still-live database. They're harmless, empty
        # group roles with no data of their own; leaving them for the life
        # of the Postgres process (a fresh container in CI, or manually via
        # `DROP ROLE amu_writer, amu_agent_base` locally) is the safe
        # default. `_grant_admin_on_shared_roles` below is what makes reusing
        # a pre-existing pair across instances/sessions safe.
    admin.close()


@pytest.fixture()
def db(test_database, admin_conn_info):
    """Truncate governance/data tables and drop any roles a previous test
    created, so each test starts from a schema-only, data-empty database.

    Role cleanup runs as the superuser admin connection, not the ephemeral
    owner_role: t_role_* is a cluster-global namespace shared with any
    sibling `test_database` instance alive elsewhere in this same pytest
    process (see the comment in test_database's teardown above), so a
    stale role this instance's own owner_role didn't create itself (and
    therefore has no ADMIN OPTION on) can still turn up in the LIKE scan --
    only the superuser can unconditionally drop those.
    """
    conn = psycopg.connect(test_database["dsn"], autocommit=True)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(DATA_TABLES) + " CASCADE")
    conn.close()

    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 't\\_role\\_%' ESCAPE '\\'")
        for (rolname,) in cur.fetchall():
            cur.execute(f'DROP ROLE IF EXISTS "{rolname}"')
    admin.close()
    return test_database
    conn.close()
    return test_database


def _make_role_dsn(base_conn_info: dict, role: str, password: str) -> str:
    return psycopg.conninfo.make_conninfo(**{**base_conn_info, "user": role, "password": password})


@pytest.fixture()
def make_agent(db):
    """Factory: make_agent("Finance") -> (role_name, dsn) for a fresh LOGIN
    role that's a member of amu_agent_base and mapped to that department."""
    admin = psycopg.connect(db["dsn"], autocommit=True)

    def _make(department: str) -> tuple[str, str]:
        suffix = secrets.token_hex(4)
        role = f"t_role_agent_{suffix}"
        password = secrets.token_hex(8)
        with admin.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_agent_base").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            cur.execute(
                "INSERT INTO amu.role_departments (role_name, department) VALUES (%s, %s)",
                (role, department),
            )
        return role, _make_role_dsn(db["conn_info"], role, password)

    yield _make
    admin.close()


@pytest.fixture()
def writer_dsn(db) -> str:
    """A real LOGIN role that's a member of amu_writer -- mirrors how the
    trusted interception layer connects in production (never SET ROLE from
    the owner)."""
    suffix = secrets.token_hex(4)
    role = f"t_role_writer_{suffix}"
    password = secrets.token_hex(8)
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_writer").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    admin.close()
    return _make_role_dsn(db["conn_info"], role, password)


def insert_amu(
    dsn: str,
    *,
    metric_name: str,
    description: str,
    value: dict,
    owner_department: str,
    epoch: int,
    lineage: dict,
    definition_hash: str,
    embedding: list[float] | None = None,
) -> dict:
    """Insert one AMU as whichever role `dsn` connects as (normally the
    writer) and return the trigger-computed lineage fields."""
    from pgvector.psycopg import register_vector
    from psycopg.types.json import Json

    if embedding is None:
        embedding = [0.1] * TEST_EMBEDDING_DIM

    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO amu.memory_units
                (metric_name, description, value, owner_department, epoch,
                 lineage, definition_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, lineage_status, lineage_columns, sensitive_columns
            """,
            (
                metric_name,
                description,
                Json(value),
                owner_department,
                epoch,
                Json(lineage),
                definition_hash,
                embedding,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return {
            "id": row[0],
            "lineage_status": row[1],
            "lineage_columns": row[2],
            "sensitive_columns": row[3],
        }
