"""Filtered recall benchmark: recall@10 of gated HNSW search (amu.search(),
hnsw.iterative_scan=relaxed_order) against exact brute-force search over
the same role's actually-visible rows, at visible fractions 50%/10%/1%.

Ground truth is computed by disabling the HNSW index scan path for that
query (SET enable_indexscan/enable_bitmapscan = off, forcing a sequential
scan with exact distance ordering) over the *same* RLS-gated table, as the
*same* role -- so both sides see the identical visible set, and any recall
gap is attributable to the HNSW approximation, not a difference in what's
being searched.

Usage: uv run python benchmarks/recall.py --visible-fractions 0.5 0.1 0.01
       [--n 50000] [--queries 100] [--k 10] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

EMBEDDING_DIM = 1536


def exact_topk(conn: psycopg.Connection, query_vec: list[float], k: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SET enable_indexscan = off")
        cur.execute("SET enable_bitmapscan = off")
        cur.execute(
            "SELECT id FROM amu.memory_units ORDER BY embedding <=> %s::vector LIMIT %s",
            (query_vec, k),
        )
        rows = [str(r[0]) for r in cur.fetchall()]
        cur.execute("RESET enable_indexscan")
        cur.execute("RESET enable_bitmapscan")
        return rows


def approx_topk(conn: psycopg.Connection, query_vec: list[float], k: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM amu.search(%s::vector, %s)", (query_vec, k))
        return [str(r[0]) for r in cur.fetchall()]


def run_one_fraction(n: int, visible_fraction: float, n_queries: int, k: int, seed_value: int) -> dict:
    rng = np.random.default_rng(seed_value)
    suffix = f"recall{int(visible_fraction * 1000)}"
    db = common.install_benchmark_database(embedding_dim=EMBEDDING_DIM, suffix=suffix)
    try:
        common.seed_workload(
            db, n=n, embedding_dim=EMBEDDING_DIM, visible_fraction=visible_fraction, seed=seed_value
        )
        role_dsn = common.make_agent_role(db, "t_role_agent_recall", "Restricted")
        conn = psycopg.connect(role_dsn, autocommit=True)
        common.register_vector(conn)

        recalls = []
        for _ in range(n_queries):
            v = rng.normal(size=EMBEDDING_DIM).astype("float32")
            v /= np.linalg.norm(v)
            query_vec = v.tolist()

            truth = set(exact_topk(conn, query_vec, k))
            approx = set(approx_topk(conn, query_vec, k))
            recalls.append(len(truth & approx) / len(truth) if truth else 1.0)

        conn.close()
        return {
            "n_amus": n,
            "visible_fraction": visible_fraction,
            "n_visible_approx": int(round(n * visible_fraction)),
            "k": k,
            "n_queries": n_queries,
            "recall_at_k_mean": round(sum(recalls) / len(recalls), 4),
            "recall_at_k_min": round(min(recalls), 4),
        }
    finally:
        common.drop_benchmark_database(db)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visible-fractions", type=float, nargs="+", default=[0.5, 0.1, 0.01])
    parser.add_argument("--n", type=int, default=50_000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    admin_conn = psycopg.connect(common.ADMIN_DSN)
    out_dir = common.new_results_dir("recall")
    all_results = []

    for fraction in args.visible_fractions:
        print(f"=== recall benchmark: n={args.n} visible_fraction={fraction} ===", flush=True)
        t0 = time.time()
        result = run_one_fraction(args.n, fraction, args.queries, args.k, args.seed)
        result["wall_clock_seconds"] = round(time.time() - t0, 1)
        all_results.append(result)
        print(json.dumps(result, indent=2), flush=True)

    (out_dir / "results.json").write_text(json.dumps(all_results, indent=2, sort_keys=True) + "\n")
    common.write_manifest(
        out_dir,
        conn=admin_conn,
        params={
            "n_amus": args.n,
            "visible_fractions": args.visible_fractions,
            "n_queries": args.queries,
            "k": args.k,
            "embedding_dim": EMBEDDING_DIM,
            "seed": args.seed,
        },
    )
    admin_conn.close()
    print(f"\nWrote {out_dir}")


if __name__ == "__main__":
    main()
