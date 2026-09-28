"""amu.normalize_ident() must match amu_governance.sql_lineage._normalize()
byte-for-byte -- that's the whole basis for keying amu.sensitive_columns on
bare column names identically to GovernancePolicy (see docs/design.md).
"""

from __future__ import annotations

import psycopg
import pytest
from amu_governance.sql_lineage import _normalize

CASES = [
    "income",
    "Income",
    "  income  ",
    '"income"',
    "'income'",
    "[income]",
    "Customer ID",
    "customer_id",
    "  \"Weird [Column]\"  ",
    "already_normalized",
    "MiXeD Case Name",
    "a b   c",
    "'\"[mixed quoting]\"'",
]


@pytest.mark.parametrize("raw", CASES)
def test_normalize_matches_python(db, raw):
    admin = psycopg.connect(db["dsn"], autocommit=True)
    with admin.cursor() as cur:
        cur.execute("SELECT amu.normalize_ident(%s)", (raw,))
        sql_result = cur.fetchone()[0]
    admin.close()
    assert sql_result == _normalize(raw)
