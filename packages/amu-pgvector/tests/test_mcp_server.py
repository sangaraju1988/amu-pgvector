"""amu-pgvector-mcp's three tools, called directly as the plain Python
functions @mcp.tool leaves them as (verified: the decorator registers but
does not wrap them) -- this exercises the real AMUStore/Postgres RLS path
under whichever role's DSN the server is configured with, same as the rest
of the suite. What's NOT covered here is the MCP JSON-RPC protocol
serialization itself (list_tools/call_tool wire format) -- that's the SDK's
own well-tested internals, not this project's code.
"""

from __future__ import annotations

import psycopg
import pytest
from amu_pgvector import mcp_server
from amu_pgvector.embeddings import fake_embedder
from amu_pgvector.store import AMUStore

EMBED = fake_embedder(dim=32)


@pytest.fixture(autouse=True)
def _reset_mcp_server_globals(monkeypatch):
    monkeypatch.setattr(mcp_server, "_store", None)
    monkeypatch.setattr(mcp_server, "_embed_fn", EMBED)
    yield


def test_amu_search_and_record_are_gated(db, writer_dsn, make_agent, monkeypatch):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES ('income')")
        cur.execute(
            "INSERT INTO amu.department_permissions (department, column_id) VALUES ('Finance', 'income')"
        )
    admin.close()

    monkeypatch.setattr(mcp_server, "_store", AMUStore(writer_dsn))
    record_result = mcp_server.amu_record(
        sql="SELECT avg(income) FROM customers",
        value={"avg": 50000},
        metric_name="avg_income",
        description="average customer income",
        owner_department="Finance",
    )
    assert record_result["lineage_status"] == "resolved"

    _, mkt_dsn = make_agent("Marketing")
    monkeypatch.setattr(mcp_server, "_store", AMUStore(mkt_dsn))
    mkt_hits = mcp_server.amu_search(query="average customer income", k=5)
    assert all(h["metric_name"] != "avg_income" for h in mkt_hits)

    _, fin_dsn = make_agent("Finance")
    monkeypatch.setattr(mcp_server, "_store", AMUStore(fin_dsn))
    fin_hits = mcp_server.amu_search(query="average customer income", k=5)
    assert any(h["metric_name"] == "avg_income" for h in fin_hits)


def test_amu_record_denied_for_non_writer_role(db, make_agent, monkeypatch):
    _, mkt_dsn = make_agent("Marketing")
    monkeypatch.setattr(mcp_server, "_store", AMUStore(mkt_dsn))
    with pytest.raises(Exception, match="permission denied"):
        mcp_server.amu_record(
            sql="SELECT count(*) FROM signups",
            value={"count": 1},
            metric_name="signup_count",
            description="weekly signups",
            owner_department="Marketing",
        )


def test_amu_check_conflict_returns_only_department_and_hash(db, writer_dsn, monkeypatch):
    monkeypatch.setattr(mcp_server, "_store", AMUStore(writer_dsn))
    mcp_server.amu_record(
        sql="SELECT sum(amount) FROM transactions WHERE region = 'US'",
        value={"sum": 100},
        metric_name="revenue",
        description="US revenue",
        owner_department="Finance",
    )
    conflicts = mcp_server.amu_check_conflict(
        metric_name="revenue", definition_hash="zzzzzzzzzzzz", owner_department="Marketing"
    )
    assert conflicts == [{"department": "Finance", "definition_hash": conflicts[0]["definition_hash"]}]
    flat = str(conflicts)
    assert "100" not in flat
    assert "transactions" not in flat


def test_missing_dsn_env_var_raises_clear_error(monkeypatch):
    monkeypatch.delenv(mcp_server.DSN_ENV_VAR, raising=False)
    monkeypatch.setattr(mcp_server, "_store", None)
    with pytest.raises(RuntimeError, match=mcp_server.DSN_ENV_VAR):
        mcp_server._get_store()
