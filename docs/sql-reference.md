# SQL reference

Exhaustive reference for every object `sql/amu_pgvector.sql` creates, in
schema `amu`. See `docs/design.md` for the reasoning behind each design
choice and `docs/threat-model.md` for what the gate does and doesn't cover.

## Install-time variables

Passed via `psql -v name=value`:

| Variable | Default | Meaning |
|---|---|---|
| `embedding_dim` | `1536` | Width of `amu.memory_units.embedding` (`vector(embedding_dim)`). Fixed at install time; changing it later requires a manual `ALTER TABLE ... ALTER COLUMN embedding TYPE vector(N)` and rebuilding the HNSW index. |
| `convenience_mode_enabled` | `off` | `on`/`off`. See "Identity" below and `docs/threat-model.md`. |

## Tables

### `amu.sensitive_columns`

| Column | Type | Notes |
|---|---|---|
| `column_id` | `text` PK | Bare, normalized column name (via `amu.normalize_ident`) -- **not** `table.column`. See `docs/design.md`'s "Divergence found" note: this matches `amu_governance.GovernancePolicy.sensitive_columns` exactly, which is also bare-name and schema-wide. |

Insert/delete fires `trg_reclassify_insert`/`trg_reclassify_delete`, which
recomputes `sensitive_columns` on every affected `amu.memory_units` row
without rewriting them by hand.

### `amu.department_permissions`

| Column | Type | Notes |
|---|---|---|
| `department` | `text` | Free text, not a foreign key to anything -- there's no separate department registry. |
| `column_id` | `text` | Part of the composite PK `(department, column_id)`. The full row set for a department is P(d). |

### `amu.role_departments`

| Column | Type | Notes |
|---|---|---|
| `role_name` | `name` PK | A Postgres role name. |
| `department` | `text` NOT NULL | Looked up by `amu.permitted_columns()` keyed on `session_user` (strong mode). |

### `amu.materialization_edges`

| Column | Type | Notes |
|---|---|---|
| `derived_table` | `text` | Part of PK `(derived_table, source_table)`. Both are normalized via `amu.normalize_ident` when they originate from lineage jsonb, but **not** automatically when inserted directly here -- register edges using the same table-name spelling `amu_governance.sql_lineage` would extract, or use `AMUStore.register_materialization_edge()`, which doesn't re-normalize either; keep names consistent. |
| `source_table` | `text` | |
| `column_map` | `jsonb` | `{"derived_col": "source_col"}` or `{"derived_col": ["source_col1", "source_col2"]}`. A derived column with no entry here passes through unchanged in closure (see `amu.close_lineage`) rather than being dropped. |

### `amu.memory_units`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK, default `gen_random_uuid()` | The real primary key everywhere except LangChain's id-addressed contract. |
| `external_id` | `text`, nullable, unique when non-null | Caller-supplied correlation id (`langchain_amu`'s document ids). `NULL` for AMUs written via `AMUStore.record()` directly. |
| `metric_name` | `text` NOT NULL | Not unique -- multiple AMUs (different departments, different epochs) can share a metric_name; `amu.check_conflict()` is how you find out they disagree. |
| `description` | `text` NOT NULL | The natural-language text that gets embedded. |
| `value` | `jsonb` NOT NULL | The cached analytical result. Opaque to the gate. |
| `owner_department` | `text` NOT NULL | Who derived this AMU. |
| `epoch` | `bigint` NOT NULL | Caller-supplied ordering/versioning integer; not security-relevant. |
| `created_at` | `timestamptz` NOT NULL, default `now()` | |
| `lineage` | `jsonb` NOT NULL | `{"steps": [{"table": ..., "columns_used": [...]}], "filter_logic": ...}` -- the serialized form of `amu_governance.Lineage`. |
| `lineage_columns` | `text[]` NOT NULL | **Server-computed, never trusted from the client.** The full closure of `lineage` over `materialization_edges` (or the raw, un-closed columns if unresolved). GIN-indexed. |
| `sensitive_columns` | `text[]` NOT NULL | **Server-computed.** `lineage_columns ∩ amu.sensitive_columns`. This is S(a). |
| `definition_hash` | `text` NOT NULL, `CHECK (~ '^[0-9a-f]{12}$')` | From `amu_governance.Lineage.definition_hash()`. Client-computed but format-validated. |
| `lineage_status` | `text` NOT NULL, `CHECK (IN ('resolved','unresolved'))` | **Server-computed.** `'unresolved'` rows are invisible to everyone via RLS, regardless of `sensitive_columns`. |
| `embedding` | `vector(embedding_dim)` | HNSW-indexed, cosine ops. |

`lineage_columns`, `sensitive_columns` and `lineage_status` are recomputed
by `trg_compute_lineage_fields` (`BEFORE INSERT OR UPDATE`) from `lineage`
on *every* write -- whatever a client sends for these three columns is
discarded. This is what makes a forged array unable to bypass the gate.

Indexes: `memory_units_lineage_columns_gin` (GIN on `lineage_columns`,
used by reclassification), `memory_units_metric_name_idx`,
`memory_units_owner_department_idx`, `memory_units_embedding_hnsw`
(cosine), `memory_units_external_id_uidx` (partial unique).

### `amu.install_settings`

| Column | Type | Notes |
|---|---|---|
| `key` | `text` PK | Currently only `'convenience_mode_enabled'`. |
| `value` | `text` NOT NULL | |

Not meant to be queried directly by application code -- `amu.permitted_columns()` reads it internally.

## Functions

| Function | Security | Notes |
|---|---|---|
| `amu.normalize_ident(text) → text` | INVOKER, IMMUTABLE | Must match `amu_governance.sql_lineage._normalize` byte-for-byte (parity-tested). |
| `amu.close_lineage(jsonb, int DEFAULT 16) → text[]` | INVOKER, STABLE, `jit=off` | Recursive closure over `materialization_edges`. Returns `NULL` (never partial) on a cycle or a chain deeper than the depth argument. Append-only: a mapped column's own name stays in the result alongside whatever it resolves to. |
| `amu._raw_lineage_columns(jsonb) → text[]` | INVOKER, IMMUTABLE | Flattened, normalized, un-closed column set -- used only for the `lineage_columns` value on unresolved rows. |
| `amu._compute_lineage_fields() → trigger` | **DEFINER** | The `BEFORE INSERT OR UPDATE` trigger function. DEFINER so `amu_writer` doesn't need direct SELECT on `sensitive_columns`/`materialization_edges`. |
| `amu._reclassify_memory_units() → trigger` | INVOKER | `AFTER INSERT/DELETE ... FOR EACH STATEMENT` on `sensitive_columns`, run by whoever changes the registry (normally the owner). |
| `amu.permitted_columns() → text[]` | **DEFINER**, STABLE | Returns P(d) for the calling role. `session_user`-keyed in strong mode; consults `SET LOCAL amu.department` first only when convenience mode is enabled at install time. Empty set for an unknown role/department. |
| `amu.search(vector, int, text DEFAULT NULL) → TABLE(...)` | **INVOKER**, STABLE | The retrieval entry point. `SET hnsw.iterative_scan = 'relaxed_order'` so filtered results still hit k. RLS applies because it's SECURITY INVOKER. Cast the embedding argument explicitly (`%s::vector`) from client code -- see `docs/design.md`. |
| `amu.check_conflict(text, text, text) → TABLE(conflicting_department, other_hash)` | **DEFINER**, STABLE | Cross-department by nature; returns only department+hash, resolved rows only. |

## Roles

| Role | Attributes | Privileges |
|---|---|---|
| `amu_writer` | `NOLOGIN NOBYPASSRLS` | `INSERT, UPDATE, DELETE, SELECT` on `memory_units` (its own unconditional SELECT policy, `USING (true)`), `EXECUTE` on `search`/`permitted_columns`/`check_conflict`. No access to metadata tables. |
| `amu_agent_base` | `NOLOGIN NOBYPASSRLS` | `SELECT` on `memory_units` (gated by `memory_units_select`), `EXECUTE` on the same three functions. No access to metadata tables. |

Real agents/writer processes connect as a LOGIN role that's a member of
one of these two (`CREATE ROLE ... IN ROLE amu_writer` /
`IN ROLE amu_agent_base`), plus (for agents) a row in
`amu.role_departments`. Neither base role is ever the table owner. Both
are cluster-global role *names* -- see `docs/design.md`'s note on running
more than one amu-pgvector install on the same Postgres instance.

## RLS policies on `amu.memory_units`

| Policy | Command | To | Condition |
|---|---|---|---|
| `memory_units_owner_all` | ALL | `CURRENT_USER` (resolved at install time) | `true` |
| `memory_units_select` | SELECT | public | `lineage_status = 'resolved' AND sensitive_columns <@ amu.permitted_columns()` |
| `memory_units_writer_insert` | INSERT | `amu_writer` | `true` (WITH CHECK) |
| `memory_units_writer_select` | SELECT | `amu_writer` | `true` |
| `memory_units_writer_update` | UPDATE | `amu_writer` | `true` |
| `memory_units_writer_delete` | DELETE | `amu_writer` | `true` |

`FORCE ROW LEVEL SECURITY` is set, so even the table owner is subject to
these (via the `memory_units_owner_all` carve-out) -- see `docs/design.md`.

## Uninstalling

`sql/uninstall.sql` drops the `amu` schema (cascades to everything in it)
and the two base roles. It does not touch the `vector` extension or any
per-agent/per-writer LOGIN roles you created.
