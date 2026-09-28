"""The project's own guarantee, on top of whatever the generic standard
suite checks: a restricted role never gets a gated document back through
similarity_search, max_marginal_relevance_search, or AMURetriever -- because
all three run SQL under that role's own connection, so Postgres RLS applies
exactly as it does everywhere else in this project."""

from __future__ import annotations

import psycopg
import pytest
from amu_pgvector import AMUStore
from conftest import TEST_EMBEDDING_DIM
from langchain_amu import AMURetriever, AMUVectorStore
from langchain_core.embeddings import DeterministicFakeEmbedding

EMBEDDING = DeterministicFakeEmbedding(size=TEST_EMBEDDING_DIM)


@pytest.fixture()
def gated_setup(db, writer_dsn, make_agent):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES ('income')")
        cur.execute(
            "INSERT INTO amu.department_permissions (department, column_id) VALUES ('Finance', 'income')"
        )
    admin.close()

    writer_store = AMUStore(writer_dsn)
    writer_store.record(
        "SELECT avg(income) FROM customers",
        {"avg": 50000},
        metric_name="avg_income",
        description="average customer income by segment",
        owner_department="Finance",
        embed_fn=EMBEDDING.embed_query,
    )
    writer_store.record(
        "SELECT count(*) FROM signups",
        {"count": 10},
        metric_name="signup_count",
        description="weekly signup count by segment",
        owner_department="Marketing",
        embed_fn=EMBEDDING.embed_query,
    )

    _, mkt_dsn = make_agent("Marketing")
    _, fin_dsn = make_agent("Finance")
    return {
        "mkt_store": AMUVectorStore(AMUStore(mkt_dsn), EMBEDDING),
        "fin_store": AMUVectorStore(AMUStore(fin_dsn), EMBEDDING),
    }


def test_similarity_search_never_returns_gated_document(gated_setup):
    mkt_docs = gated_setup["mkt_store"].similarity_search("customer income by segment", k=5)
    assert all(d.metadata["metric_name"] != "avg_income" for d in mkt_docs)

    fin_docs = gated_setup["fin_store"].similarity_search("customer income by segment", k=5)
    assert any(d.metadata["metric_name"] == "avg_income" for d in fin_docs)


def test_max_marginal_relevance_search_never_returns_gated_document(gated_setup):
    mkt_docs = gated_setup["mkt_store"].max_marginal_relevance_search(
        "customer income by segment", k=5, fetch_k=10
    )
    assert all(d.metadata["metric_name"] != "avg_income" for d in mkt_docs)

    fin_docs = gated_setup["fin_store"].max_marginal_relevance_search(
        "customer income by segment", k=5, fetch_k=10
    )
    assert any(d.metadata["metric_name"] == "avg_income" for d in fin_docs)


def test_retriever_never_returns_gated_document(gated_setup):
    mkt_retriever = AMURetriever(vectorstore=gated_setup["mkt_store"], k=5)
    mkt_docs = mkt_retriever.invoke("customer income by segment")
    assert all(d.metadata["metric_name"] != "avg_income" for d in mkt_docs)

    fin_retriever = AMURetriever(vectorstore=gated_setup["fin_store"], k=5)
    fin_docs = fin_retriever.invoke("customer income by segment")
    assert any(d.metadata["metric_name"] == "avg_income" for d in fin_docs)
