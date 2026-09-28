"""Reuses the amu-pgvector test suite's fixtures (ephemeral Postgres
database + non-superuser owner role, installed from the real
sql/amu_pgvector.sql, plus writer/agent role factories) -- langchain-amu is
tested against the exact same live schema, not a second copy of it.

Sibling test packages in this monorepo aren't on sys.path by default (each
package's tests/ is its own pytest rootless-import scope), and both
conftest.py files share the same basename -- a plain `from conftest import
...` here would try to re-import this very module. Loading the other one
under an explicit distinct module name sidesteps both problems.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "amu-pgvector" / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("amu_pgvector_tests_conftest", _path)
_amu_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_amu_conftest)

TEST_EMBEDDING_DIM = _amu_conftest.TEST_EMBEDDING_DIM
insert_amu = _amu_conftest.insert_amu
admin_conn_info = _amu_conftest.admin_conn_info
test_database = _amu_conftest.test_database
db = _amu_conftest.db
make_agent = _amu_conftest.make_agent
writer_dsn = _amu_conftest.writer_dsn
