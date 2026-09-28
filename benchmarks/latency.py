"""Latency benchmark: p50/p95 search latency at k=10, for each dataset
size in --sizes, with RLS off vs on and hnsw.iterative_scan off vs on --
same role, same queries, same underlying data each time (RLS is toggled
with ALTER TABLE ... [DISABLE|ENABLE] ROW LEVEL SECURITY on the one
already-seeded table, not by re-seeding), so any latency difference is
attributable to the thing actually being varied. Writes one results.json
+ manifest.json per dataset size.

Usage: uv run python benchmarks/latency.py --sizes 10000 100000 [--queries 200] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

K = 10
VISIBLE_FRACTION = 0.5
EMBEDDING_DIM = 1536


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))
    return values[idx]


def time_queries(conn: psycopg.Connection, query_vectors: list[np.ndarray], sql_text: str) -> list[float]:
    latencies = []
    with conn.cursor() as cur:
        for vec in query_vectors:
            start = time.perf_counter()
            cur.execute(sql_text, (vec.tolist(), K))
            cur.fetchall()
            latencies.append((time.perf_counter() - start) * 1000)
    return latencies


AMU_SEARCH_SQL = "SELECT id FROM amu.search(%s::vector, %s)"
# No manual WHERE clause at all -- when RLS is enabled, Postgres applies
# memory_units_select's condition transparently; when RLS is disabled
# (ALTER TABLE ... DISABLE ROW LEVEL SECURITY), this exact same query
# returns everything unfiltered. That's what makes "RLS off" a true
# apples-to-apples baseline rather than a hand-written predicate that
# would filter regardless of the table's actual RLS state.
RAW_SQL_NO_ITERATIVE = "SELECT id FROM amu.memory_units ORDER BY embedding <=> %s::vector LIMIT %s"


def run_one_size(n: int, n_queries: int, seed_value: int) -> dict:
    rng = np.random.default_rng(seed_value)
    db = common.install_benchmark_database(embedding_dim=EMBEDDING_DIM, suffix=f"lat{n}")
    try:
        common.seed_workload(
            db, n=n, embedding_dim=EMBEDDING_DIM, visible_fraction=VISIBLE_FRACTION, seed=seed_value
        )
        role_dsn = common.make_agent_role(db, "t_role_agent_latency", "Restricted")
        # autocommit: SET/RESET hnsw.iterative_scan and the timed SELECTs
        # must never sit inside an open transaction -- an idle-in-
        # transaction snapshot on this connection would hold a lock that
        # blocks the owner's ALTER TABLE ... DISABLE ROW LEVEL SECURITY
        # below, deadlocking the benchmark against itself.
        conn = psycopg.connect(role_dsn, autocommit=True)
        common.register_vector(conn)

        query_vectors = []
        for _ in range(n_queries):
            v = rng.normal(size=EMBEDDING_DIM).astype("float32")
            v /= np.linalg.norm(v)
            query_vectors.append(v)

        results: dict[str, dict] = {}

        # RLS on, iterative scan on (amu.search()'s own SET clause).
        results["rls_on_iterative_on"] = time_queries(conn, query_vectors, AMU_SEARCH_SQL)

        # RLS on, iterative scan off (session default, i.e. plain SELECT
        # without amu.search()'s per-call SET hnsw.iterative_scan).
        with conn.cursor() as cur:
            cur.execute("SET hnsw.iterative_scan = 'off'")
        results["rls_on_iterative_off"] = time_queries(conn, query_vectors, RAW_SQL_NO_ITERATIVE)
        with conn.cursor() as cur:
            cur.execute("RESET hnsw.iterative_scan")

        owner_conn = psycopg.connect(db["dsn"], autocommit=True)
        with owner_conn.cursor() as cur:
            cur.execute("ALTER TABLE amu.memory_units DISABLE ROW LEVEL SECURITY")
        owner_conn.close()

        results["rls_off_iterative_on"] = time_queries(conn, query_vectors, AMU_SEARCH_SQL)
        with conn.cursor() as cur:
            cur.execute("SET hnsw.iterative_scan = 'off'")
        results["rls_off_iterative_off"] = time_queries(conn, query_vectors, RAW_SQL_NO_ITERATIVE)
        with conn.cursor() as cur:
            cur.execute("RESET hnsw.iterative_scan")

        owner_conn = psycopg.connect(db["dsn"], autocommit=True)
        with owner_conn.cursor() as cur:
            cur.execute("ALTER TABLE amu.memory_units ENABLE ROW LEVEL SECURITY")
        owner_conn.close()

        conn.close()

        summary = {
            name: {
                "p50_ms": round(percentile(lat, 50), 3),
                "p95_ms": round(percentile(lat, 95), 3),
                "mean_ms": round(statistics.mean(lat), 3),
                "n": len(lat),
            }
            for name, lat in results.items()
        }
        return {"n_amus": n, "k": K, "visible_fraction": VISIBLE_FRACTION, "conditions": summary}
    finally:
        common.drop_benchmark_database(db)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000])
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    admin_conn = psycopg.connect(common.ADMIN_DSN)

    for n in args.sizes:
        print(f"=== running latency benchmark at n={n} ===", flush=True)
        t0 = time.time()
        result = run_one_size(n, args.queries, args.seed)
        elapsed = time.time() - t0
        result["wall_clock_seconds"] = round(elapsed, 1)

        out_dir = common.new_results_dir("latency")
        (out_dir / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        common.write_manifest(
            out_dir,
            conn=admin_conn,
            params={
                "n_amus": n,
                "k": K,
                "n_queries": args.queries,
                "visible_fraction": VISIBLE_FRACTION,
                "embedding_dim": EMBEDDING_DIM,
                "seed": args.seed,
            },
        )
        print(json.dumps(result, indent=2))
        print(f"Wrote {out_dir} (took {elapsed:.1f}s)\n", flush=True)

    admin_conn.close()


if __name__ == "__main__":
    main()
