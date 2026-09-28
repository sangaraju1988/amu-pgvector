# Design notes

## amu-governance 0.1.2 API inspection (Phase 1)

Before designing any SQL or Python, we installed the published package and
read its source, per the project's ground rules: reuse must be byte-identical
to the paper's reference library, not reimplemented.

```
uv venv /tmp/amu_gov_inspect --python 3.12
uv pip install --python /tmp/amu_gov_inspect/bin/python amu-governance==0.1.2
```

The installed wheel's `src/amu_governance/*.py` is byte-identical (`diff`
clean) to the local `amu-governance` repo checkout at the commit tagged
`0.1.2` in `pyproject.toml`, so the local checkout can be used interchangeably
for reading, but the SQL/Python we ship must depend on the PyPI release, not
a path dependency.

### Public surface (`amu_governance/__init__.py`)

```python
from amu_governance import AMU, Lineage, LineageStep, GovernancePolicy
from amu_governance import LineageAwareSystem, NaiveMemorySystem, NoMemorySystem, RetrievalResult
from amu_governance.sql_lineage import lineage_from_sql, extract_lineage_from_sql, lineage_summary
```

Note: the installed wheel's `__version__` string is `"0.1.1"` even though
`pyproject.toml` and the PyPI release are `0.1.2` — a stale version string in
the upstream package itself. Not ours to fix; noted here so a future reader
isn't confused by `amu_governance.__version__ != "0.1.2"`.

### `model.py`

- `LineageStep(table: str, columns_used: Tuple[str, ...])` — one (table, columns) hop.
  `.sensitive_columns(policy)` intersects `columns_used` with `policy.sensitive_columns`.
- `Lineage(steps: Tuple[LineageStep, ...], filter_logic: str)`
  - `.all_columns()` → flat `Set[str]` of every column across every step (table
    association is **not preserved** in this set).
  - `.sensitive_columns(policy)` → `all_columns() & policy.sensitive_columns`.
  - `.definition_hash()` → `sha256(f"{sorted(tables)}|{sorted(all_columns())}|{filter_logic}")[:12]`.
    This is a **method on `Lineage`**, not a free function — the ground rules'
    phrase "reuse ... `definition_hash()`" means `Lineage.definition_hash()` /
    `AMU.definition_hash` (a property that delegates to it), not a standalone
    `amu_governance.definition_hash()`.
- `AMU(metric_name, value: float, owner_department, lineage: Lineage, epoch: int)`
  - `.sensitivity_tags(policy)` → `lineage.sensitive_columns(policy)`.
  - `.definition_hash` (property) → `lineage.definition_hash()`.
  - `value` is typed `float` here. The spec's `amu.memory_units.value jsonb` is
    intentionally more general (a metric can be a scalar, a vector, or a small
    table); the Python client narrows/serializes to whatever the caller passes
    and stores it as `jsonb`, independent of this constraint.

### `policy.py`

- `GovernancePolicy(sensitive_columns: Set[str], department_permissions: Dict[str, Set[str]])`
  - **`sensitive_columns` is a flat set of bare column names**, e.g. `{"income", "ssn"}` —
    *not* `table.column` identifiers. A column named `income` is sensitive in
    every table it appears in, uniformly, across the whole schema.
  - `.permitted_columns(department)` → `department_permissions.get(department, set())`.
    Unknown departments get `set()` — fail closed, matches the SQL gate's
    `amu.permitted_columns()` requirement in the spec.
  - `.sensitivity_of(columns)` → `columns & sensitive_columns`.

### `sql_lineage.py`

- `_normalize(name)` (private): lowercase, strip `"`/`'`/`[`/`]`, collapse
  whitespace to `_`. Applied **separately** to table names and column names
  when parsing SQL — it never joins them into a `table.column` string.
- `extract_lineage_from_sql(sql, dialect="sqlite")` → `[{"table": str, "columns": [str]}]`,
  built by walking the sqlglot AST, resolving aliases to real table names.
  Unqualified columns in a single-table query resolve to that table.
  Unqualified columns in a multi-table query that can't be resolved bucket
  under the synthetic table `"unknown"` — **kept, never dropped**, so an
  unresolved reference to a sensitive column still counts as touched
  (fail-closed by design; this is the fix from commit `dc8e960` in the sibling
  `Lineage-Aware-Memory` repo, now upstream in 0.1.2).
  Never raises: unparseable SQL returns `[]`.
- `lineage_from_sql(sql, filter_logic="", dialect="sqlite")` → `Lineage`, built
  directly from `extract_lineage_from_sql`. Default dialect is **SQLite**; we
  will call this with `dialect="postgres"` for our writer-side lineage
  extraction (the executed SQL is Postgres, not SQLite).
- `lineage_summary(steps)` → human-readable one-liner, for logging.

### `systems.py`

`LineageAwareSystem.request()` is the reference gate we must be
bit-for-bit consistent with in the parity test (§ Tests, spec):

```python
safe = None
for amu in reversed(candidates):            # most recent first
    if amu.sensitivity_tags(policy) - permitted:   # S(a) - P(d) nonempty => blocked
        continue
    safe = amu
    break
```

i.e. an AMU is servable iff `S(a) ⊆ P(d)`. This is exactly the SQL gate's
`sensitive_columns <@ amu.permitted_columns()` (Postgres `<@` is "is contained
by", the array analogue of `⊆`).

### Divergence found, and the decision made

The spec (§1) describes `amu.sensitive_columns(column_id text PRIMARY KEY)` as
storing a fully-qualified `table.column` string "normalized with the same
function amu_governance.sql_lineage uses." No such function exists — sql_lineage
normalizes table names and column names as independent tokens and the library's
sensitivity check (`GovernancePolicy.sensitive_columns`) is bare-column-name,
schema-wide.

**Decision (confirmed with the project owner, Phase 1):** match the library
exactly. `amu.sensitive_columns.column_id` stores bare, normalized column names
(via the same `_normalize` lowercase/strip/whitespace-collapse rule), not
`table.column`. A column is sensitive across every table it appears in. Per-table
provenance is still retained in `memory_units.lineage` (jsonb) and
`lineage_columns` for lineage closure (§4) and audit, but it is **not** part of
the sensitivity key. This keeps `definition_hash` and the gate decision
byte-identical to `LineageAwareSystem`, satisfying the parity test.

## Paper concept → SQL object mapping

*(Filled in as each phase lands; see docs/sql-reference.md once Phase 2 ships
for the authoritative, exhaustive version.)*

| Paper / library concept | SQL object |
|---|---|
| `GovernancePolicy.sensitive_columns` | `amu.sensitive_columns` |
| `GovernancePolicy.department_permissions` (P(d)) | `amu.department_permissions` |
| `GovernancePolicy.permitted_columns(department)` | `amu.permitted_columns()` (STABLE SECURITY DEFINER, keyed off `session_user` via `amu.role_departments`) |
| `AMU` | `amu.memory_units` row |
| `AMU.sensitivity_tags(policy)` (S(a)) | `amu.memory_units.sensitive_columns` (trigger-maintained) |
| `Lineage` / `LineageStep` | `amu.memory_units.lineage` (jsonb) + `.lineage_columns` (text[]) |
| `Lineage.definition_hash()` | `amu.memory_units.definition_hash` (computed client-side via `amu_governance`, stored, format-validated) |
| `LineageAwareSystem.request()` gate (`S(a) ⊆ P(d)`) | RLS `SELECT` policy: `sensitive_columns <@ amu.permitted_columns()` |
| Materialized/derived-table lineage expansion | `amu.materialization_edges` + `amu.close_lineage()` |
