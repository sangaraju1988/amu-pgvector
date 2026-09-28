"""Core RLS security tests: S(a) subset P(d) enforced by Postgres itself.

Every test connects as a real, distinct Postgres role -- never mocked, never
filtered in application code -- so a failure here means the database itself
would leak, not just a client library bug.
"""

from __future__ import annotations

import psycopg
import pytest

from conftest import insert_amu

FINANCE_LINEAGE = {
    "steps": [{"table": "customers", "columns_used": ["income", "customer_id"]}],
    "filter_logic": "avg(income)",
}
MARKETING_LINEAGE = {
    "steps": [{"table": "signups", "columns_used": ["signup_date", "customer_id"]}],
    "filter_logic": "count(*)",
}


@pytest.fixture()
def seeded(db, writer_dsn, make_agent):
    """One sensitive AMU (Finance/income) and one non-sensitive AMU
    (Marketing), plus Finance and Marketing agent roles/connections."""
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES ('income')")
        cur.execute(
            "INSERT INTO amu.department_permissions (department, column_id) VALUES ('Finance', 'income')"
        )
    admin.close()

    fin_amu = insert_amu(
        writer_dsn,
        metric_name="avg_income",
        description="average customer income",
        value={"avg": 50000},
        owner_department="Finance",
        epoch=1,
        lineage=FINANCE_LINEAGE,
        definition_hash="0123456789ab",
    )
    mkt_amu = insert_amu(
        writer_dsn,
        metric_name="signup_count",
        description="weekly signup count",
        value={"count": 120},
        owner_department="Marketing",
        epoch=1,
        lineage=MARKETING_LINEAGE,
        definition_hash="aaaaaaaaaaaa",
    )

    _, fin_dsn = make_agent("Finance")
    _, mkt_dsn = make_agent("Marketing")

    return {"fin_amu": fin_amu, "mkt_amu": mkt_amu, "fin_dsn": fin_dsn, "mkt_dsn": mkt_dsn}


def test_restricted_role_cannot_select_gated_row(seeded):
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.memory_units WHERE metric_name = 'avg_income'")
        assert cur.fetchall() == []


def test_restricted_role_count_excludes_gated_row(seeded):
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM amu.memory_units")
        assert cur.fetchone()[0] == 1  # only its own non-sensitive AMU


def test_restricted_role_exists_excludes_gated_row(seeded):
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM amu.memory_units WHERE metric_name = 'avg_income')")
        assert cur.fetchone()[0] is False


def test_restricted_role_cannot_see_gated_row_through_own_view(seeded):
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP VIEW my_view AS SELECT * FROM amu.memory_units")
        cur.execute("SELECT metric_name FROM my_view")
        rows = {r[0] for r in cur.fetchall()}
        assert "avg_income" not in rows
        assert rows == {"signup_count"}


def test_permitted_role_sees_both_rows(seeded):
    with psycopg.connect(seeded["fin_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT metric_name FROM amu.memory_units ORDER BY metric_name")
        assert [r[0] for r in cur.fetchall()] == ["avg_income", "signup_count"]


def test_search_function_is_gated(seeded):
    from pgvector.psycopg import register_vector

    with psycopg.connect(seeded["mkt_dsn"]) as conn:
        register_vector(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT metric_name FROM amu.search(%s::vector, 10)",
            ([0.1] * 32,),
        )
        rows = {r[0] for r in cur.fetchall()}
        assert "avg_income" not in rows


def test_set_department_has_no_effect_in_strong_mode(seeded):
    """Convenience mode is off by default; SET amu.department must be a
    no-op for an agent role in strong (session_user-based) mode."""
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SET amu.department = 'Finance'")
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == []
        cur.execute("SELECT count(*) FROM amu.memory_units")
        assert cur.fetchone()[0] == 1


def test_unresolved_lineage_invisible_to_everyone_including_owner_department(db, writer_dsn, make_agent):
    """A lineage step referencing an unregistered derived table with no
    materialization edge closes fine (nothing to expand); to force
    'unresolved' we need a real cycle/depth-exceeded chain (see
    test_closure.py). Here we exercise the same invisibility guarantee via
    the trigger's unresolved path directly."""
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES ('cyc_a', 'cyc_b', '{\"x\":\"x\"}'), ('cyc_b', 'cyc_a', '{\"x\":\"x\"}')"
        )
        cur.execute(
            "INSERT INTO amu.department_permissions (department, column_id) VALUES ('Finance', 'x')"
        )
    admin.close()

    amu = insert_amu(
        writer_dsn,
        metric_name="unresolved_metric",
        description="cyclic derivation",
        value={"v": 1},
        owner_department="Finance",
        epoch=1,
        lineage={"steps": [{"table": "cyc_a", "columns_used": ["x"]}], "filter_logic": "n/a"},
        definition_hash="ffffffffffff",
    )
    assert amu["lineage_status"] == "unresolved"

    _, fin_dsn = make_agent("Finance")  # same department that owns it
    with psycopg.connect(fin_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.memory_units WHERE metric_name = 'unresolved_metric'")
        assert cur.fetchall() == []


def test_reclassification_hides_previously_visible_amu(seeded, db):
    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.memory_units WHERE metric_name = 'signup_count'")
        assert len(cur.fetchall()) == 1

    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES ('signup_date')")
    admin.close()

    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.memory_units WHERE metric_name = 'signup_count'")
        assert cur.fetchall() == []  # hidden without rewriting the row

    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("DELETE FROM amu.sensitive_columns WHERE column_id = 'signup_date'")
    admin.close()

    with psycopg.connect(seeded["mkt_dsn"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.memory_units WHERE metric_name = 'signup_count'")
        assert len(cur.fetchall()) == 1  # restored, again without a rewrite


def test_table_owner_is_not_used_for_agents(db):
    """The install script's own owner-bypass policy must be scoped to the
    installing role, never to a role agents connect as."""
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('amu_writer', 'amu_agent_base')"
        )
        role_names = {r[0] for r in cur.fetchall()}
        assert role_names == {"amu_writer", "amu_agent_base"}

        cur.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('amu_writer', 'amu_agent_base')"
        )
        for rolname, rolsuper, rolbypassrls in cur.fetchall():
            assert rolsuper is False, rolname
            assert rolbypassrls is False, rolname

        cur.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname = 'amu' AND tablename = 'memory_units'"
        )
        owner = cur.fetchone()[0]
        assert owner not in ("amu_writer", "amu_agent_base")
    admin.close()


def test_check_conflict_returns_no_values_or_lineage(seeded):
    with psycopg.connect(seeded["fin_dsn"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM amu.check_conflict('avg_income', %s, 'Marketing')",
            ("zzzzzzzzzzzz",),
        )
        cols = [d.name for d in cur.description]
        assert set(cols) == {"conflicting_department", "other_hash"}
        rows = cur.fetchall()
        assert rows == [("Finance", "0123456789ab")]
        # Belt and suspenders: the sensitive value (50000) and the raw
        # lineage table/column names never appear anywhere in the result.
        flat = str(rows)
        assert "50000" not in flat
        assert "income" not in flat
        assert "customers" not in flat
