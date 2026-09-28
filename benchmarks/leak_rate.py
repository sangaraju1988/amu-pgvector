"""Leak-rate benchmark: replay a synthetic multi-department workload
against (a) a naive, content-gated retrieval policy (keyed on metric_name
only, no lineage check -- representative of existing governed-memory
systems that gate on access tags rather than derivation, mirroring
amu_governance.NaiveMemorySystem) and (b) amu-pgvector's real RLS gate.
Report cross-department leaks measured for each condition -- not assumed.

A "leak" is: the AMU actually returned to a requester has a
sensitive_columns set that is NOT a subset of that requester's permitted
columns (S(a) not subset of P(d)).

Usage: uv run python benchmarks/leak_rate.py [--n-metrics 60] [--writes-per-metric 10]
       [--requests 2000] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

DEPARTMENTS = ["Finance", "Marketing", "Support"]
SENSITIVE_COLUMNS = ["income", "ssn", "email"]
SAFE_COLUMNS = ["customer_id", "region", "txn_id", "amount"]

DEPARTMENT_PERMISSIONS = {
    "Finance": set(SENSITIVE_COLUMNS) | set(SAFE_COLUMNS),
    "Marketing": set(SAFE_COLUMNS),
    "Support": {"customer_id", "region"},
}


def build_metric_catalog(n_metrics: int, rng: random.Random) -> list[dict]:
    catalog = []
    for i in range(n_metrics):
        sensitive = i % 2 == 0
        column = rng.choice(SENSITIVE_COLUMNS) if sensitive else rng.choice(SAFE_COLUMNS)
        catalog.append({"metric_name": f"metric_{i}", "column": column, "sensitive": sensitive})
    return catalog


def seed(db: dict, catalog: list[dict], writes_per_metric: int, rng: random.Random) -> None:
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        for col in SENSITIVE_COLUMNS:
            cur.execute(
                "INSERT INTO amu.sensitive_columns (column_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (col,),
            )
        for dept, cols in DEPARTMENT_PERMISSIONS.items():
            for col in cols:
                cur.execute(
                    "INSERT INTO amu.department_permissions (department, column_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (dept, col),
                )
    admin.close()

    writer_dsn = common.make_writer_role(db, "t_role_writer_leak")
    conn = psycopg.connect(writer_dsn)
    common.register_vector(conn)
    with conn.cursor() as cur:
        epoch = 0
        for entry in catalog:
            for _ in range(writes_per_metric):
                # Sensitive metrics are always genuinely derived from a
                # Finance-only column, so any non-Finance write of the
                # "same" metric name is necessarily a different (safe)
                # derivation -- exactly the definition-conflict shape the
                # paper models, and irrelevant to leak measurement here.
                owner_department = "Finance" if entry["sensitive"] else rng.choice(DEPARTMENTS)
                lineage = {
                    "steps": [{"table": "bench_table", "columns_used": [entry["column"]]}],
                    "filter_logic": f"epoch {epoch}",
                }
                cur.execute(
                    """
                    INSERT INTO amu.memory_units
                        (metric_name, description, value, owner_department, epoch,
                         lineage, definition_hash, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry["metric_name"],
                        f"{entry['metric_name']} description",
                        json.dumps({"v": epoch}),
                        owner_department,
                        epoch,
                        json.dumps(lineage),
                        f"{epoch:012x}"[:12],
                        [rng.random() + 0.001 for _ in range(8)],
                    ),
                )
                epoch += 1
    conn.commit()
    conn.close()


def naive_most_recent(admin_conn: psycopg.Connection, metric_name: str) -> tuple[str, list[str]] | None:
    """Bypasses RLS entirely (superuser connection) and picks the most
    recently written AMU for metric_name regardless of department -- the
    NaiveMemorySystem policy: content/tag-gated, no lineage check."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT owner_department, sensitive_columns FROM amu.memory_units "
            "WHERE metric_name = %s ORDER BY epoch DESC LIMIT 1",
            (metric_name,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None


def gated_most_recent(agent_conn: psycopg.Connection, metric_name: str) -> tuple[str, list[str]] | None:
    with agent_conn.cursor() as cur:
        cur.execute(
            "SELECT owner_department, sensitive_columns FROM amu.memory_units "
            "WHERE metric_name = %s ORDER BY epoch DESC LIMIT 1",
            (metric_name,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None


def run(n_metrics: int, writes_per_metric: int, n_requests: int, seed_value: int) -> dict:
    rng = random.Random(seed_value)
    db = common.install_benchmark_database(embedding_dim=8, suffix=f"leak{seed_value}")
    try:
        catalog = build_metric_catalog(n_metrics, rng)
        seed(db, catalog, writes_per_metric, rng)

        agent_conns = {}
        for dept in DEPARTMENTS:
            dsn = common.make_agent_role(db, f"t_role_agent_leak_{dept.lower()}", dept)
            agent_conns[dept] = psycopg.connect(dsn)
        admin_conn = psycopg.connect(db["dsn"])

        requester_pool = ["Marketing", "Support"]  # the two restricted departments
        naive_leaks = 0
        gated_leaks = 0
        served_naive = 0
        served_gated = 0

        for _ in range(n_requests):
            requester = rng.choice(requester_pool)
            entry = rng.choice(catalog)
            permitted = DEPARTMENT_PERMISSIONS[requester]

            naive = naive_most_recent(admin_conn, entry["metric_name"])
            if naive is not None:
                served_naive += 1
                _, sensitive_cols = naive
                if not set(sensitive_cols) <= permitted:
                    naive_leaks += 1

            gated = gated_most_recent(agent_conns[requester], entry["metric_name"])
            if gated is not None:
                served_gated += 1
                _, sensitive_cols = gated
                if not set(sensitive_cols) <= permitted:
                    gated_leaks += 1

        admin_conn.close()
        for c in agent_conns.values():
            c.close()

        return {
            "n_metrics": n_metrics,
            "writes_per_metric": writes_per_metric,
            "n_amus_written": n_metrics * writes_per_metric,
            "n_requests": n_requests,
            "naive": {
                "served": served_naive,
                "leaks": naive_leaks,
                "leak_rate": naive_leaks / n_requests,
            },
            "gated": {
                "served": served_gated,
                "leaks": gated_leaks,
                "leak_rate": gated_leaks / n_requests,
            },
        }
    finally:
        common.drop_benchmark_database(db)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-metrics", type=int, default=60)
    parser.add_argument("--writes-per-metric", type=int, default=10)
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    results = run(args.n_metrics, args.writes_per_metric, args.requests, args.seed)

    out_dir = common.new_results_dir("leak_rate")
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    conn = psycopg.connect(common.ADMIN_DSN)
    common.write_manifest(
        out_dir,
        conn=conn,
        params={
            "n_metrics": args.n_metrics,
            "writes_per_metric": args.writes_per_metric,
            "n_requests": args.requests,
            "seed": args.seed,
            "departments": DEPARTMENTS,
            "sensitive_columns": SENSITIVE_COLUMNS,
            "department_permissions": {k: sorted(v) for k, v in DEPARTMENT_PERMISSIONS.items()},
        },
    )
    conn.close()

    print(json.dumps(results, indent=2))
    print(f"\nWrote {out_dir}")


if __name__ == "__main__":
    main()
