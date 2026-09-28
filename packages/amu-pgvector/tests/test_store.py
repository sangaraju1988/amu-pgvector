"""AMUStore end-to-end against real Postgres: the RLS gate must hold the
same way through the Python client as it does through raw SQL (test_rls_
security.py already proves the raw-SQL side; this proves the client
doesn't accidentally add a bypass, e.g. by trusting a client-supplied
sensitive_columns array)."""

from __future__ import annotations

import psycopg
import pytest
from amu_pgvector import AMUStore
from amu_pgvector.embeddings import fake_embedder

EMBED = fake_embedder(dim=32)  # matches conftest.TEST_EMBEDDING_DIM


@pytest.fixture()
def owner_store(db) -> AMUStore:
    return AMUStore(db["dsn"])


@pytest.fixture()
def writer_store(writer_dsn) -> AMUStore:
    return AMUStore(writer_dsn)


def test_record_and_search_roundtrip(db, owner_store, writer_store, make_agent):
    owner_store.grant_department_permission("Marketing", "customer_id")
    result = writer_store.record(
        "SELECT customer_id, count(*) FROM signups GROUP BY customer_id",
        {"count": 42},
        metric_name="signup_count",
        description="signups grouped by customer",
        owner_department="Marketing",
        embed_fn=EMBED,
    )
    assert result.lineage_status == "resolved"
    assert result.conflicts == []

    _, mkt_dsn = make_agent("Marketing")
    hits = AMUStore(mkt_dsn).search("signups grouped by customer", k=5, embed_fn=EMBED)
    assert any(h.id == result.amu_id for h in hits)


def test_record_gates_sensitive_amu_through_the_client(
    db, owner_store, writer_store, make_agent
):
    owner_store.register_sensitive_column("income")
    owner_store.grant_department_permission("Finance", "income")

    fin_result = writer_store.record(
        "SELECT avg(income) FROM customers",
        {"avg": 50000},
        metric_name="avg_income",
        description="average customer income",
        owner_department="Finance",
        embed_fn=EMBED,
    )
    assert fin_result.lineage_status == "resolved"

    _, mkt_dsn = make_agent("Marketing")
    _, fin_dsn = make_agent("Finance")

    mkt_hits = AMUStore(mkt_dsn).search("average customer income", k=5, embed_fn=EMBED)
    assert all(h.id != fin_result.amu_id for h in mkt_hits)

    fin_hits = AMUStore(fin_dsn).search("average customer income", k=5, embed_fn=EMBED)
    assert any(h.id == fin_result.amu_id for h in fin_hits)


def test_record_surfaces_conflicting_definition(db, owner_store, writer_store):
    owner_store.grant_department_permission("Finance", "amount")
    owner_store.grant_department_permission("Marketing", "amount")

    first = writer_store.record(
        "SELECT sum(amount) FROM transactions WHERE region = 'US'",
        {"sum": 100},
        metric_name="revenue",
        description="US revenue",
        owner_department="Finance",
        embed_fn=EMBED,
    )
    assert first.conflicts == []

    second = writer_store.record(
        "SELECT sum(amount) FROM transactions",  # different filter -> different hash
        {"sum": 500},
        metric_name="revenue",
        description="total revenue",
        owner_department="Marketing",
        embed_fn=EMBED,
    )
    assert len(second.conflicts) == 1
    assert second.conflicts[0].department == "Finance"


def test_record_surfaces_unresolved_lineage_status(db, owner_store, writer_store):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES ('cyc_a', 'cyc_b', '{\"y\":\"y\"}'), ('cyc_b', 'cyc_a', '{\"y\":\"y\"}')"
        )
    admin.close()

    result = writer_store.record(
        "SELECT y FROM cyc_a",
        {"v": 1},
        metric_name="cyclic_metric",
        description="cyclic derivation",
        owner_department="Finance",
        embed_fn=EMBED,
    )
    assert result.lineage_status == "unresolved"


def test_admin_create_agent_role_wires_up_permitted_columns(db, owner_store):
    owner_store.register_sensitive_column("ssn")
    owner_store.grant_department_permission("Support", "ssn")
    owner_store.create_agent_role("t_role_agent_admin_test", "Support", "pw123456")

    conn_info = {**db["conn_info"], "user": "t_role_agent_admin_test", "password": "pw123456"}
    dsn = psycopg.conninfo.make_conninfo(**conn_info)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT amu.permitted_columns()")
        assert cur.fetchone()[0] == ["ssn"]


def test_record_with_external_id_upserts_in_place(db, writer_store):
    first = writer_store.record(
        "SELECT count(*) FROM signups",
        {"count": 1},
        metric_name="signup_count",
        description="v1 of the description",
        owner_department="Marketing",
        embed_fn=EMBED,
        external_id="doc-1",
    )
    second = writer_store.record(
        "SELECT count(*) FROM signups",
        {"count": 2},
        metric_name="signup_count",
        description="v2 of the description",
        owner_department="Marketing",
        embed_fn=EMBED,
        external_id="doc-1",
    )
    assert first.amu_id == second.amu_id  # same row, upserted, not duplicated

    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("SELECT count(*) FROM amu.memory_units WHERE external_id = 'doc-1'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT description, value FROM amu.memory_units WHERE external_id = 'doc-1'")
        description, value = cur.fetchone()
        assert description == "v2 of the description"
        assert value == {"count": 2}
    admin.close()


def test_delete_and_get_by_external_ids(db, writer_store):
    writer_store.record(
        "SELECT count(*) FROM signups",
        {"count": 1},
        metric_name="signup_count",
        description="d1",
        owner_department="Marketing",
        embed_fn=EMBED,
        external_id="del-1",
    )
    assert len(writer_store.get_by_external_ids(["del-1"])) == 1

    writer_store.delete_by_external_ids(["del-1"])
    assert writer_store.get_by_external_ids(["del-1"]) == []

    # Deleting something already gone must not raise.
    writer_store.delete_by_external_ids(["del-1", "never-existed"])


def test_get_by_external_ids_is_gated(db, owner_store, writer_store, make_agent):
    owner_store.register_sensitive_column("income")
    owner_store.grant_department_permission("Finance", "income")

    writer_store.record(
        "SELECT avg(income) FROM customers",
        {"avg": 1},
        metric_name="avg_income",
        description="gated",
        owner_department="Finance",
        embed_fn=EMBED,
        external_id="gated-1",
    )

    _, mkt_dsn = make_agent("Marketing")
    assert AMUStore(mkt_dsn).get_by_external_ids(["gated-1"]) == []

    _, fin_dsn = make_agent("Finance")
    assert len(AMUStore(fin_dsn).get_by_external_ids(["gated-1"])) == 1


def test_admin_register_materialization_edge_feeds_closure(db, owner_store, writer_store):
    owner_store.register_sensitive_column("ssn")
    owner_store.grant_department_permission("Finance", "ssn")
    owner_store.register_materialization_edge(
        "customer_summary", "customers_raw", {"masked_id": "ssn"}
    )

    result = writer_store.record(
        "SELECT masked_id FROM customer_summary",
        {"v": 1},
        metric_name="summary_metric",
        description="derived from a materialized view",
        owner_department="Finance",
        embed_fn=EMBED,
    )
    assert result.lineage_status == "resolved"

    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "SELECT sensitive_columns FROM amu.memory_units WHERE id = %s", (result.amu_id,)
        )
        assert cur.fetchone()[0] == ["ssn"]
    admin.close()
