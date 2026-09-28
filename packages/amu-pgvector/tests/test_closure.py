"""Lineage closure over materializations (spec section 4).

Never truncates silently: a chain longer than max_depth, or a cycle, must
come back as unresolved (NULL from amu.close_lineage, lineage_status =
'unresolved' on the row) rather than a partially-expanded column set that
would look safe when it isn't.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import insert_amu


def _build_chain(cur, prefix: str, hops: int) -> None:
    """derived_table c{prefix}_0 -> ... -> c{prefix}_{hops} (the base table),
    identity column_map at every hop, so closing c{prefix}_0 needs exactly
    `hops` edge traversals to bottom out."""
    for i in range(hops):
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (f"c{prefix}_{i}", f"c{prefix}_{i + 1}", psycopg.types.json.Json({"x": "x"})),
        )


@pytest.mark.parametrize("hops", [1, 8, 9, 16])
def test_chain_within_default_max_depth_resolves(db, hops):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        _build_chain(cur, str(hops), hops)
        cur.execute(
            "SELECT amu.close_lineage(%s::jsonb)",
            (f'{{"steps":[{{"table":"c{hops}_0","columns_used":["x"]}}]}}',),
        )
        result = cur.fetchone()[0]
    admin.close()
    assert result == ["x"]


def test_chain_exceeding_default_max_depth_is_unresolved(db):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        _build_chain(cur, "17", 17)
        cur.execute(
            "SELECT amu.close_lineage(%s::jsonb)",
            ('{"steps":[{"table":"c17_0","columns_used":["x"]}]}',),
        )
        result = cur.fetchone()[0]
    admin.close()
    assert result is None


def test_same_chain_resolves_with_a_larger_explicit_max_depth(db):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        _build_chain(cur, "17b", 17)
        cur.execute(
            "SELECT amu.close_lineage(%s::jsonb, 17)",
            ('{"steps":[{"table":"c17b_0","columns_used":["x"]}]}',),
        )
        result = cur.fetchone()[0]
    admin.close()
    assert result == ["x"]


def test_cycle_is_unresolved_not_infinite_loop(db):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES ('cyc_a', 'cyc_b', '{\"y\":\"y\"}'), ('cyc_b', 'cyc_a', '{\"y\":\"y\"}')"
        )
        cur.execute(
            "SELECT amu.close_lineage(%s::jsonb)",
            ('{"steps":[{"table":"cyc_a","columns_used":["y"]}]}',),
        )
        result = cur.fetchone()[0]
    admin.close()
    assert result is None


def test_unresolved_closure_marks_the_amu_row_unresolved(db, writer_dsn):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES ('cyc_a', 'cyc_b', '{\"y\":\"y\"}'), ('cyc_b', 'cyc_a', '{\"y\":\"y\"}')"
        )
    admin.close()

    row = insert_amu(
        writer_dsn,
        metric_name="m",
        description="d",
        value={"v": 1},
        owner_department="Finance",
        epoch=1,
        lineage={"steps": [{"table": "cyc_a", "columns_used": ["y"]}], "filter_logic": "n/a"},
        definition_hash="bbbbbbbbbbbb",
    )
    assert row["lineage_status"] == "unresolved"
    # Fail closed: everything touched is treated as sensitive when we can't
    # prove otherwise, even though the row is already hidden regardless.
    assert row["sensitive_columns"] == row["lineage_columns"]


def test_unmapped_derived_column_passes_through_rather_than_dropping(db):
    """A derived table with a registered edge but no column_map entry for a
    specific column must keep that column (bare name) in the closure --
    never silently drop it, which would under-report sensitivity. Mapped
    columns keep their own (derived-table) name too, alongside the source
    column their mapping adds -- closure only ever adds columns to the
    touched set, never removes the ones it started from."""
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.materialization_edges (derived_table, source_table, column_map) "
            "VALUES ('derived_t', 'base_t', '{\"mapped_col\":\"base_col\"}')"
        )
        cur.execute(
            "SELECT amu.close_lineage(%s::jsonb)",
            (
                '{"steps":[{"table":"derived_t","columns_used":'
                '["mapped_col","untracked_col"]}]}',
            ),
        )
        result = set(cur.fetchone()[0])
    admin.close()
    assert result == {"mapped_col", "base_col", "untracked_col"}
