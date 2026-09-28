"""Convenience mode (SET LOCAL amu.department on a pooled connection) is an
install-time opt-in, off by default -- test_rls_security.py already proves
it's a no-op when off. This module installs a SEPARATE database with it
turned on, to prove it actually works when a deployment explicitly wants it,
and that it still fails closed for an unmapped/empty department.
"""

from __future__ import annotations

import secrets
import subprocess

import psycopg
import pytest
from conftest import (
    ADMIN_DSN,
    SQL_FILE,
    TEST_EMBEDDING_DIM,
    _conninfo_dict,
    _grant_admin_on_shared_roles,
    _pg_env,
    _psql_path,
)
from psycopg import sql


@pytest.fixture(scope="module")
def convenience_db():
    admin_conn_info = _conninfo_dict(ADMIN_DSN)
    suffix = secrets.token_hex(4)
    db_name = f"amu_conv_{suffix}"
    owner_role = f"amu_conv_owner_{suffix}"
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

    admin_for_db = psycopg.connect(**{**admin_conn_info, "dbname": db_name}, autocommit=True)
    with admin_for_db.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    admin_for_db.close()

    owner_conn_info = {
        **admin_conn_info,
        "dbname": db_name,
        "user": owner_role,
        "password": owner_password,
    }
    subprocess.run(
        [
            _psql_path(), "-v", "ON_ERROR_STOP=1",
            "-v", f"embedding_dim={TEST_EMBEDDING_DIM}",
            "-v", "convenience_mode_enabled=on",
            "-f", str(SQL_FILE),
        ],
        env=_pg_env(owner_conn_info),
        check=True,
    )

    _grant_admin_on_shared_roles(admin_conn_info, owner_role)

    dsn = psycopg.conninfo.make_conninfo(**owner_conn_info)

    # A single pooled login role, used the way a trusted application would
    # use one -- one shared role, department picked per-request via SET
    # LOCAL, never via a per-department Postgres role.
    pooled_role = f"amu_conv_pooled_{suffix}"
    pooled_password = secrets.token_hex(8)
    admin = psycopg.connect(dsn, autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_agent_base").format(
                sql.Identifier(pooled_role), sql.Literal(pooled_password)
            )
        )
        cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES ('income')")
        cur.execute(
            "INSERT INTO amu.department_permissions (department, column_id) VALUES ('Finance', 'income')"
        )
    admin.close()

    pooled_dsn = psycopg.conninfo.make_conninfo(
        **{**owner_conn_info, "user": pooled_role, "password": pooled_password}
    )

    yield {"dsn": dsn, "pooled_dsn": pooled_dsn}

    # amu_writer/amu_agent_base are shared, cluster-global roles that the
    # session-scoped `test_database` fixture in conftest.py may still be
    # using elsewhere in this same pytest session -- only its teardown
    # (which runs last) drops them.
    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        cur.execute(f'DROP ROLE IF EXISTS "{pooled_role}"')
        cur.execute(f'DROP ROLE IF EXISTS "{owner_role}"')
    admin.close()


def test_set_local_department_grants_permitted_columns_when_enabled(convenience_db):
    with psycopg.connect(convenience_db["pooled_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL amu.department = 'Finance'")
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == ["income"]
        cur.execute("COMMIT")


def test_no_set_local_still_fails_closed_when_enabled(convenience_db):
    """Convenience mode being on doesn't change strong-mode behavior when
    the trusted app simply doesn't set a department -- unknown identity
    still gets the empty set, never a default-open one."""
    with psycopg.connect(convenience_db["pooled_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == []


def test_set_local_scope_does_not_leak_across_transactions(convenience_db):
    with psycopg.connect(convenience_db["pooled_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL amu.department = 'Finance'")
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == ["income"]
        cur.execute("COMMIT")

        # New transaction, same session, no SET LOCAL this time.
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == []
