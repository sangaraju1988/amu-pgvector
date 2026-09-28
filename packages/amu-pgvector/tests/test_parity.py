"""Reference-correctness parity: for the paper's synthetic scenario (ported
from amu_governance's own test fixtures), the SQL gate's allow/deny decision
must match amu_governance.LineageAwareSystem on every request.

Both sides replay the identical write sequence, then for every
(metric_name, requester_department) pair we compare "is there a safe,
most-recently-written candidate" -- LineageAwareSystem.request(...).reused
against a real RLS-gated SELECT as that department's own Postgres role.
"""

from __future__ import annotations

import psycopg
import pytest
from amu_governance import AMU, GovernancePolicy, Lineage, LineageAwareSystem, LineageStep

from conftest import insert_amu

POLICY = GovernancePolicy(
    sensitive_columns={"ssn", "email", "income"},
    department_permissions={
        "Finance": {"ssn", "income", "email", "customer_id", "region", "txn_id", "amount"},
        "Marketing": {"customer_id", "region", "txn_id", "amount"},
        "Support": {"customer_id", "region"},
    },
)
DEPARTMENTS = ["Finance", "Marketing", "Support"]

# (metric_name, table, columns_used, filter_logic) -- mirrors
# amu-governance/tests/test_systems.py's _sensitive_amu/_safe_amu plus one
# extra metric so more than one department can write competing definitions.
DEFINITIONS = {
    "high_value_segment": ("customers", ("customer_id", "income"), "income > 100000"),
    "order_volume": ("transactions", ("txn_id", "amount"), "all transactions"),
    "support_ticket_rate": ("tickets", ("customer_id", "region"), "count(*) by region"),
}

# Write sequence: (metric_name, owner_department, epoch). Each write goes to
# both systems in this exact order so "most recent" means the same thing on
# both sides.
WRITES = [
    ("high_value_segment", "Finance", 0),
    ("order_volume", "Finance", 1),
    ("order_volume", "Marketing", 2),
    ("support_ticket_rate", "Support", 3),
    ("high_value_segment", "Marketing", 4),  # conflicting definition, see below
]


def _python_amu(metric_name: str, owner_department: str, epoch: int) -> AMU:
    table, columns, filter_logic = DEFINITIONS[metric_name]
    if metric_name == "high_value_segment" and owner_department == "Marketing":
        # Marketing can't see income -- its own version of this metric is
        # necessarily derived differently (matches test_systems.py's
        # definition-conflict scenario).
        table, columns, filter_logic = "transactions", ("customer_id", "amount"), "amount > 10000"
    lineage = Lineage(steps=(LineageStep(table, columns),), filter_logic=filter_logic)
    return AMU(metric_name, 1.0, owner_department, lineage, epoch)


def _sql_lineage(metric_name: str, owner_department: str) -> dict:
    amu = _python_amu(metric_name, owner_department, 0)
    step = amu.lineage.steps[0]
    return {
        "steps": [{"table": step.table, "columns_used": list(step.columns_used)}],
        "filter_logic": amu.lineage.filter_logic,
    }


@pytest.fixture()
def parity_setup(db, writer_dsn, make_agent):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        for col in POLICY.sensitive_columns:
            cur.execute("INSERT INTO amu.sensitive_columns (column_id) VALUES (%s)", (col,))
        for dept, cols in POLICY.department_permissions.items():
            for col in cols:
                cur.execute(
                    "INSERT INTO amu.department_permissions (department, column_id) VALUES (%s, %s)",
                    (dept, col),
                )
    admin.close()

    py_system = LineageAwareSystem(POLICY)
    for metric_name, owner_department, epoch in WRITES:
        py_amu = _python_amu(metric_name, owner_department, epoch)
        py_system.write(py_amu)
        insert_amu(
            writer_dsn,
            metric_name=metric_name,
            description=f"{metric_name} by {owner_department}",
            value={"v": epoch},
            owner_department=owner_department,
            epoch=epoch,
            lineage=_sql_lineage(metric_name, owner_department),
            definition_hash=py_amu.definition_hash,
        )

    agent_dsns = {}
    for dept in DEPARTMENTS:
        _, dsn = make_agent(dept)
        agent_dsns[dept] = dsn

    return {"py_system": py_system, "agent_dsns": agent_dsns}


def _sql_most_recent_visible(dsn: str, metric_name: str) -> str | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT owner_department FROM amu.memory_units "
            "WHERE metric_name = %s ORDER BY epoch DESC LIMIT 1",
            (metric_name,),
        )
        row = cur.fetchone()
        return row[0] if row else None


@pytest.mark.parametrize("metric_name", list(DEFINITIONS))
@pytest.mark.parametrize("requester_department", DEPARTMENTS)
def test_gate_decision_matches_reference_system(parity_setup, metric_name, requester_department):
    py_system = parity_setup["py_system"]
    fresh = _python_amu(metric_name, requester_department, epoch=999)
    py_result = py_system.request(metric_name, requester_department, fresh)

    sql_served_by = _sql_most_recent_visible(
        parity_setup["agent_dsns"][requester_department], metric_name
    )

    if py_result.reused:
        assert sql_served_by is not None, (
            f"{requester_department} should be served {metric_name} from memory, "
            f"but Postgres returned nothing"
        )
        assert sql_served_by == py_result.source_department
    else:
        assert sql_served_by is None, (
            f"{requester_department} should NOT be served {metric_name} from memory "
            f"(blocked or no candidate), but Postgres returned a row owned by {sql_served_by}"
        )
