"""Shared setup for the leak-rate/latency/recall benchmarks: an ephemeral
benchmark database installed from the real sql/amu_pgvector.sql, seeded
with a synthetic workload where the visible fraction for a 'Restricted'
role is exactly controllable (needed for the recall-at-visible-fraction
sweep), plus manifest.json bookkeeping (git SHA, Postgres/pgvector
versions, hardware, seed, parameters) per the project's ground rules:
every number in results/ must trace back to a run that actually happened.
"""

from __future__ import annotations

import json
import platform
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = REPO_ROOT / "sql" / "amu_pgvector.sql"

ADMIN_DSN = "postgresql://amu_owner:amu_owner_password@localhost:5433/amu_dev"

SENSITIVE_COLUMN = "secret_col"
SAFE_COLUMN = "public_col"
DEPARTMENTS = ["Alpha", "Beta", "Gamma"]


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def postgres_version(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version")
        return cur.fetchone()[0]


def pgvector_version(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
        return row[0] if row else "unknown"


def hardware_info() -> dict:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python_version": sys.version.split()[0],
    }
    try:
        cpu_count = subprocess.run(
            ["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, check=True
        ).stdout.strip()
        mem_bytes = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
        ).stdout.strip()
        info["host_cpu_count"] = int(cpu_count)
        info["host_memory_gb"] = round(int(mem_bytes) / (1024**3), 1)
    except Exception:
        pass
    try:
        docker_info = subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        ncpu, mem_total = docker_info.split()
        info["docker_vm_cpu_count"] = int(ncpu)
        info["docker_vm_memory_gb"] = round(int(mem_total) / (1024**3), 1)
    except Exception:
        pass
    return info


def write_manifest(out_dir: Path, *, conn: psycopg.Connection, params: dict) -> None:
    manifest = {
        "git_sha": git_sha(),
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "postgres_version": postgres_version(conn),
        "pgvector_version": pgvector_version(conn),
        "hardware": hardware_info(),
        "parameters": params,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def new_results_dir(benchmark_name: str) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "results" / benchmark_name / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def install_benchmark_database(*, embedding_dim: int, suffix: str | None = None) -> dict:
    """Fresh ephemeral database + non-superuser owner role, schema
    installed exactly as `psql -f` would run it in production (same path
    the test suite and CI exercise)."""
    suffix = suffix or secrets.token_hex(4)
    db_name = f"amu_bench_{suffix}"
    owner_role = f"amu_bench_owner_{suffix}"
    owner_password = secrets.token_hex(8)

    admin_conn_info = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN)
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
    env = {
        **__import__("os").environ,
        "PGHOST": str(owner_conn_info.get("host", "localhost")),
        "PGPORT": str(owner_conn_info.get("port", 5432)),
        "PGUSER": owner_role,
        "PGPASSWORD": owner_password,
        "PGDATABASE": db_name,
    }
    subprocess.run(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"embedding_dim={embedding_dim}",
            "-v",
            "convenience_mode_enabled=off",
            "-f",
            str(SQL_FILE),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    # amu_writer/amu_agent_base are cluster-global role names (see
    # docs/design.md); a prior benchmark or test run's owner may already
    # have created them, in which case only the superuser can grant this
    # new owner ADMIN OPTION over them too.
    admin = psycopg.connect(**admin_conn_info, autocommit=True)
    with admin.cursor() as cur:
        for role in ("amu_writer", "amu_agent_base"):
            cur.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    sql.Identifier(role), sql.Identifier(owner_role)
                )
            )
    admin.close()

    owner_dsn = psycopg.conninfo.make_conninfo(**owner_conn_info)
    return {
        "dsn": owner_dsn,
        "conn_info": owner_conn_info,
        "admin_conn_info": admin_conn_info,
        "db_name": db_name,
        "owner_role": owner_role,
    }


def drop_benchmark_database(db: dict) -> None:
    admin = psycopg.connect(**db["admin_conn_info"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 't\\_role\\_%' ESCAPE '\\'")
        for (rolname,) in cur.fetchall():
            cur.execute(f'DROP ROLE IF EXISTS "{rolname}"')
        cur.execute(f'DROP DATABASE IF EXISTS "{db["db_name"]}" WITH (FORCE)')
        cur.execute(f'DROP ROLE IF EXISTS "{db["owner_role"]}"')
    admin.close()


def make_agent_role(db: dict, role_name: str, department: str) -> str:
    admin = psycopg.connect(db["dsn"], autocommit=True)
    password = secrets.token_hex(8)
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_agent_base").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
        cur.execute(
            "INSERT INTO amu.role_departments (role_name, department) VALUES (%s, %s) "
            "ON CONFLICT (role_name) DO NOTHING",
            (role_name, department),
        )
    admin.close()
    return psycopg.conninfo.make_conninfo(**{**db["conn_info"], "user": role_name, "password": password})


def make_writer_role(db: dict, role_name: str) -> str:
    admin = psycopg.connect(db["dsn"], autocommit=True)
    password = secrets.token_hex(8)
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE amu_writer").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
    admin.close()
    return psycopg.conninfo.make_conninfo(**{**db["conn_info"], "user": role_name, "password": password})


def seed_workload(
    db: dict,
    *,
    n: int,
    embedding_dim: int,
    visible_fraction: float,
    seed: int,
    build_hnsw_after: bool = True,
) -> None:
    """Insert n synthetic AMUs, round-robined across DEPARTMENTS. Exactly
    `visible_fraction` of rows touch only SAFE_COLUMN (visible to a
    'Restricted' role permitted nothing); the rest touch SENSITIVE_COLUMN
    (blocked for 'Restricted'). Drops the HNSW index before the bulk load
    and rebuilds it once afterward (standard pgvector bulk-load practice --
    incrementally maintaining the graph across a large COPY is far slower
    than building it once against the final data)."""
    rng = np.random.default_rng(seed)

    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO amu.sensitive_columns (column_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (SENSITIVE_COLUMN,),
        )
        for dept in DEPARTMENTS:
            cur.execute(
                "INSERT INTO amu.department_permissions (department, column_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (dept, SENSITIVE_COLUMN),
            )
        cur.execute("DROP INDEX IF EXISTS amu.memory_units_embedding_hnsw")
    admin.close()

    n_blocked = int(round(n * (1 - visible_fraction)))

    # COPY FROM is refused by Postgres on any table with row-level security
    # enabled unless the role is superuser/BYPASSRLS ("Use INSERT statements
    # instead") -- true even for our owner role, whose USING(true) policy
    # would otherwise make it a no-op check. This is purely benchmark-data
    # seeding, not the product's real write path (that's amu_writer, already
    # covered by the test suite), so using the superuser bootstrap
    # connection here is a disclosed methodology choice, not a shortcut
    # around anything the product itself relies on.
    # write_row() round-trips per row, which over Docker Desktop's
    # virtualized network stack dominates wall-clock time at any real
    # scale (confirmed: ~2.5 minutes stuck on a 10k-row COPY before this
    # was batched). None of these generated field values can contain a
    # tab/newline/backslash, so hand-formatting whole batches of COPY TEXT
    # rows and sending each batch with one copy.write() call is safe and
    # avoids that per-row round-trip entirely.
    batch_size = 5000
    conn = psycopg.connect(**{**db["admin_conn_info"], "dbname": db["db_name"]})
    with conn.cursor() as cur, cur.copy(
        "COPY amu.memory_units "
        "(metric_name, description, value, owner_department, epoch, lineage, "
        "definition_hash, embedding) FROM STDIN"
    ) as copy:
        lines: list[str] = []
        embeddings = rng.normal(size=(n, embedding_dim)).astype("float32")
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        for i in range(n):
            dept = DEPARTMENTS[i % len(DEPARTMENTS)]
            blocked = i < n_blocked
            column = SENSITIVE_COLUMN if blocked else SAFE_COLUMN
            lineage = json.dumps(
                {
                    "steps": [{"table": "bench_table", "columns_used": [column]}],
                    "filter_logic": f"bench row {i}",
                }
            )
            vec_text = "[" + ",".join(f"{x:.6f}" for x in embeddings[i]) + "]"
            lines.append(
                "\t".join(
                    [
                        f"bench_metric_{i}",
                        f"benchmark row {i}",
                        json.dumps({"v": i}),
                        dept,
                        str(i),
                        lineage,
                        f"{i:012x}"[:12],
                        vec_text,
                    ]
                )
            )
            if len(lines) >= batch_size:
                copy.write("\n".join(lines) + "\n")
                lines = []
        if lines:
            copy.write("\n".join(lines) + "\n")
    conn.commit()
    conn.close()

    if build_hnsw_after:
        admin = psycopg.connect(db["dsn"], autocommit=True)
        with admin.cursor() as cur:
            cur.execute(
                "CREATE INDEX memory_units_embedding_hnsw ON amu.memory_units "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        admin.close()
