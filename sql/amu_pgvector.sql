-- amu-pgvector: Lineage-Aware Memory Governance on PostgreSQL + pgvector.
--
-- Enforces S(a) subset P(d) via Postgres row-level security, not an
-- application-side filter. See docs/design.md and docs/threat-model.md.
--
-- Requirements: Postgres 16+, pgvector >= 0.8 (iterative index scans).
-- Runs as a normal database owner (CREATEDB/CREATEROLE, no superuser
-- required) so it installs on Supabase, Neon, and other managed Postgres.
-- Idempotent: safe to run multiple times against the same database.
--
-- Install-time variables (psql -v):
--   embedding_dim               -- vector width, default 1536
--   convenience_mode_enabled    -- 'on'/'off', default 'off' (see docs/threat-model.md)
--
-- Example:
--   psql -v embedding_dim=1536 -v convenience_mode_enabled=off -f sql/amu_pgvector.sql

\if :{?embedding_dim}
\else
\set embedding_dim 1536
\endif

\if :{?convenience_mode_enabled}
\else
\set convenience_mode_enabled 'off'
\endif

-- pgvector may live in `public` or `extensions` (Supabase convention).
-- CREATE EXTENSION IF NOT EXISTS is a no-op if it's already installed
-- anywhere on the search_path; this SET just makes sure THIS script can
-- resolve the `vector` type either way.
CREATE EXTENSION IF NOT EXISTS vector;
SET search_path TO public, extensions;

CREATE SCHEMA IF NOT EXISTS amu;
SET search_path TO amu, public, extensions;

-- =====================================================================
-- 0. Normalization helper (must match amu_governance.sql_lineage._normalize
--    byte-for-byte -- see docs/design.md "Divergence found" and the
--    parity test in tests/test_normalize_parity.py).
-- =====================================================================

CREATE OR REPLACE FUNCTION amu.normalize_ident(p_name text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  v text;
BEGIN
  IF p_name IS NULL THEN
    RETURN NULL;
  END IF;
  v := btrim(p_name);
  v := btrim(v, '"');
  v := btrim(v, '''');
  v := btrim(v, '[');
  v := btrim(v, ']');
  RETURN regexp_replace(lower(v), '\s+', '_', 'g');
END;
$$;

-- =====================================================================
-- 1. Data model
-- =====================================================================

CREATE TABLE IF NOT EXISTS amu.sensitive_columns (
    column_id text PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS amu.department_permissions (
    department text NOT NULL,
    column_id  text NOT NULL,
    PRIMARY KEY (department, column_id)
);

CREATE TABLE IF NOT EXISTS amu.role_departments (
    role_name  name PRIMARY KEY,
    department text NOT NULL
);

CREATE TABLE IF NOT EXISTS amu.materialization_edges (
    derived_table text NOT NULL,
    source_table  text NOT NULL,
    column_map    jsonb NOT NULL,
    PRIMARY KEY (derived_table, source_table)
);

CREATE TABLE IF NOT EXISTS amu.memory_units (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name       text NOT NULL,
    description       text NOT NULL,
    value             jsonb NOT NULL,
    owner_department  text NOT NULL,
    epoch             bigint NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    lineage           jsonb NOT NULL,
    lineage_columns   text[] NOT NULL DEFAULT '{}',
    sensitive_columns text[] NOT NULL DEFAULT '{}',
    definition_hash   text NOT NULL,
    lineage_status    text NOT NULL DEFAULT 'unresolved',
    embedding         vector(:embedding_dim),
    CONSTRAINT memory_units_definition_hash_format
        CHECK (definition_hash ~ '^[0-9a-f]{12}$'),
    CONSTRAINT memory_units_lineage_status_check
        CHECK (lineage_status IN ('resolved', 'unresolved'))
);

-- Added after the initial release; ADD COLUMN IF NOT EXISTS keeps a
-- from-scratch install and an upgrade of an already-installed database
-- both idempotent. Caller-supplied correlation id (e.g. langchain-amu's
-- external document ids) -- NULL for AMUs written directly through
-- AMUStore.record(), which has no notion of an external id.
ALTER TABLE amu.memory_units ADD COLUMN IF NOT EXISTS external_id text;

CREATE UNIQUE INDEX IF NOT EXISTS memory_units_external_id_uidx
    ON amu.memory_units (external_id) WHERE external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS memory_units_lineage_columns_gin
    ON amu.memory_units USING gin (lineage_columns);

CREATE INDEX IF NOT EXISTS memory_units_metric_name_idx
    ON amu.memory_units (metric_name);

CREATE INDEX IF NOT EXISTS memory_units_owner_department_idx
    ON amu.memory_units (owner_department);

CREATE INDEX IF NOT EXISTS memory_units_embedding_hnsw
    ON amu.memory_units USING hnsw (embedding vector_cosine_ops);

-- =====================================================================
-- 2. Lineage closure over materializations (spec section 4)
--
-- Expands a stored Lineage (jsonb: {"steps": [{"table", "columns_used"}, ...]})
-- through amu.materialization_edges until every touched column is traced
-- back to a base (non-derived) table, or returns NULL to signal
-- "unresolved" -- fail closed -- when a chain exceeds p_max_depth hops or
-- cycles back through a table it has already visited. Never truncates
-- silently (see docs/threat-model.md).
-- =====================================================================

CREATE OR REPLACE FUNCTION amu._raw_lineage_columns(p_lineage jsonb)
RETURNS text[]
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT COALESCE(
        ARRAY(
            SELECT DISTINCT amu.normalize_ident(col)
            FROM jsonb_array_elements(COALESCE(p_lineage -> 'steps', '[]'::jsonb)) AS s
            CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(s -> 'columns_used', '[]'::jsonb)) AS col
        ),
        '{}'::text[]
    )
$$;

CREATE OR REPLACE FUNCTION amu.close_lineage(p_lineage jsonb, p_max_depth int DEFAULT 16)
RETURNS text[]
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE base AS (
        SELECT DISTINCT
            amu.normalize_ident(s ->> 'table') AS table_name,
            amu.normalize_ident(col)           AS column_name
        FROM jsonb_array_elements(COALESCE(p_lineage -> 'steps', '[]'::jsonb)) AS s
        CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(s -> 'columns_used', '[]'::jsonb)) AS col
    ),
    expansion(table_name, column_name, depth, path, cyclic) AS (
        SELECT table_name, column_name, 0, ARRAY[table_name], false
        FROM base
        UNION ALL
        SELECT
            e.source_table,
            COALESCE(mapped.mapped_col, exp.column_name),
            exp.depth + 1,
            exp.path || e.source_table,
            e.source_table = ANY (exp.path)
        FROM expansion exp
        JOIN amu.materialization_edges e ON e.derived_table = exp.table_name
        LEFT JOIN LATERAL (
            SELECT col AS mapped_col
            FROM jsonb_array_elements_text(
                CASE
                    WHEN NOT (e.column_map ? exp.column_name) THEN '[]'::jsonb
                    WHEN jsonb_typeof(e.column_map -> exp.column_name) = 'array'
                        THEN e.column_map -> exp.column_name
                    ELSE jsonb_build_array(e.column_map -> exp.column_name)
                END
            ) AS col
        ) mapped ON true
        WHERE exp.depth < p_max_depth
          AND NOT exp.cyclic
    ),
    cycle_check AS (
        SELECT EXISTS (SELECT 1 FROM expansion WHERE cyclic) AS has_cycle
    ),
    depth_cap_check AS (
        -- A non-cyclic row still sitting at the depth cap with an outgoing
        -- edge means the true chain is longer than p_max_depth allows.
        SELECT EXISTS (
            SELECT 1
            FROM expansion exp
            JOIN amu.materialization_edges e ON e.derived_table = exp.table_name
            WHERE exp.depth = p_max_depth AND NOT exp.cyclic
        ) AS hit_cap
    )
    SELECT CASE
        WHEN (SELECT has_cycle FROM cycle_check) OR (SELECT hit_cap FROM depth_cap_check)
            THEN NULL
        ELSE (SELECT COALESCE(array_agg(DISTINCT column_name), '{}'::text[]) FROM expansion WHERE NOT cyclic)
    END
$$;

-- =====================================================================
-- 3. memory_units lineage/sensitivity trigger
--
-- lineage_columns, sensitive_columns and lineage_status are NEVER trusted
-- from the client/writer -- always recomputed here from `lineage`, so a
-- forged array in an INSERT/UPDATE statement can't be used to bypass the
-- RLS gate below.
-- =====================================================================

-- SECURITY DEFINER: amu_writer inserts rows (see policy in section 7) but
-- is deliberately NOT granted SELECT on amu.sensitive_columns or
-- amu.materialization_edges (least privilege -- it should learn sensitivity
-- only through this trigger's output, never browse the registries
-- directly). Running as the installing owner lets close_lineage() and the
-- sensitive-column lookup below resolve regardless of who's inserting.
CREATE OR REPLACE FUNCTION amu._compute_lineage_fields()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = amu, pg_catalog
AS $$
DECLARE
    v_closure text[];
BEGIN
    v_closure := amu.close_lineage(NEW.lineage);

    IF v_closure IS NULL THEN
        NEW.lineage_status := 'unresolved';
        NEW.lineage_columns := amu._raw_lineage_columns(NEW.lineage);
        -- Fail closed: we don't know the true provenance, so treat every
        -- touched column as sensitive. Moot for the RLS gate itself
        -- (unresolved rows are never visible regardless), but keeps this
        -- column honest for anything that reads it directly (audits, the
        -- Python client's admin helpers).
        NEW.sensitive_columns := NEW.lineage_columns;
    ELSE
        NEW.lineage_status := 'resolved';
        NEW.lineage_columns := v_closure;
        NEW.sensitive_columns := COALESCE(
            ARRAY(
                SELECT sc.column_id
                FROM amu.sensitive_columns sc
                WHERE sc.column_id = ANY (v_closure)
            ),
            '{}'::text[]
        );
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_compute_lineage_fields ON amu.memory_units;
CREATE TRIGGER trg_compute_lineage_fields
BEFORE INSERT OR UPDATE ON amu.memory_units
FOR EACH ROW
EXECUTE FUNCTION amu._compute_lineage_fields();

-- =====================================================================
-- 4. Reclassification: registering/unregistering a sensitive column
--    updates existing AMUs' sensitive_columns without the caller having
--    to rewrite them by hand. Uses the GIN index on lineage_columns to
--    narrow the affected set before recomputing.
-- =====================================================================

CREATE OR REPLACE FUNCTION amu._reclassify_memory_units()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(column_id) INTO v_changed FROM new_rows;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(column_id) INTO v_changed FROM old_rows;
    END IF;

    IF v_changed IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE amu.memory_units m
    SET sensitive_columns = COALESCE(
        ARRAY(
            SELECT sc.column_id
            FROM amu.sensitive_columns sc
            WHERE sc.column_id = ANY (m.lineage_columns)
        ),
        '{}'::text[]
    )
    WHERE m.lineage_status = 'resolved'
      AND m.lineage_columns && v_changed;

    RETURN NULL;
END;
$$;

-- Postgres disallows REFERENCING transition tables on a trigger that
-- covers more than one event, so INSERT and DELETE need separate triggers
-- (both call the same TG_OP-dispatching function).
DROP TRIGGER IF EXISTS trg_reclassify_insert ON amu.sensitive_columns;
CREATE TRIGGER trg_reclassify_insert
AFTER INSERT ON amu.sensitive_columns
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT
EXECUTE FUNCTION amu._reclassify_memory_units();

DROP TRIGGER IF EXISTS trg_reclassify_delete ON amu.sensitive_columns;
CREATE TRIGGER trg_reclassify_delete
AFTER DELETE ON amu.sensitive_columns
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT
EXECUTE FUNCTION amu._reclassify_memory_units();

-- =====================================================================
-- 5. amu.permitted_columns(): P(d) for the calling role.
--
-- STABLE SECURITY DEFINER with a pinned search_path, so it can read the
-- metadata tables (which agent/writer roles are NOT granted direct SELECT
-- on) regardless of caller. Identity comes from session_user (§3 "strong
-- mode"), never from anything the client can SET -- unless convenience
-- mode was explicitly enabled at install time, in which case a
-- SET LOCAL amu.department set by TRUSTED application code (never the
-- agent itself) is consulted first. See docs/threat-model.md.
--
-- Unknown role / unknown department => empty set (fail closed).
-- =====================================================================

CREATE TABLE IF NOT EXISTS amu.install_settings (
    key   text PRIMARY KEY,
    value text NOT NULL
);

INSERT INTO amu.install_settings (key, value)
VALUES ('convenience_mode_enabled', :'convenience_mode_enabled')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

CREATE OR REPLACE FUNCTION amu.permitted_columns()
RETURNS text[]
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = amu, pg_catalog
AS $$
DECLARE
    v_department text;
    v_convenience_on boolean;
BEGIN
    SELECT (value = 'on') INTO v_convenience_on
    FROM amu.install_settings WHERE key = 'convenience_mode_enabled';

    IF COALESCE(v_convenience_on, false) THEN
        v_department := nullif(current_setting('amu.department', true), '');
    END IF;

    IF v_department IS NULL THEN
        SELECT rd.department INTO v_department
        FROM amu.role_departments rd
        WHERE rd.role_name = session_user::name;
    END IF;

    IF v_department IS NULL THEN
        RETURN '{}'::text[];
    END IF;

    RETURN COALESCE(
        (SELECT array_agg(dp.column_id)
         FROM amu.department_permissions dp
         WHERE dp.department = v_department),
        '{}'::text[]
    );
END;
$$;

-- =====================================================================
-- 6. Roles (created before the policies below so `TO amu_writer` resolves).
--
-- amu_writer: the only role with INSERT on memory_units. Used solely by
-- the trusted interception layer that runs agent SQL and extracts
-- lineage from it -- agents never insert directly (docs/threat-model.md).
--
-- amu_agent_base: NOLOGIN base role every per-agent LOGIN role should be
-- GRANTed. SELECT-only (via RLS), EXECUTE on search/check_conflict/
-- permitted_columns (granted in section 9, once those functions exist).
-- Never the table owner, never BYPASSRLS.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'amu_writer') THEN
        CREATE ROLE amu_writer NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'amu_agent_base') THEN
        CREATE ROLE amu_agent_base NOLOGIN NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA amu TO amu_writer, amu_agent_base;
GRANT INSERT, UPDATE, DELETE ON amu.memory_units TO amu_writer;
GRANT SELECT ON amu.memory_units TO amu_writer;
GRANT SELECT ON amu.memory_units TO amu_agent_base;

-- =====================================================================
-- 7. Row-level security: the gate itself.
--
-- FORCE (not just ENABLE) so that even the table owner is subject to it --
-- the one carve-out is the policy below scoped to CURRENT_USER, which
-- psql resolves at install time to whichever role runs this script (the
-- deploying app's owner role, whatever its name), so admin/maintenance
-- SQL run by that same role keeps working without a hardcoded role name.
-- =====================================================================

ALTER TABLE amu.memory_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE amu.memory_units FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS memory_units_owner_all ON amu.memory_units;
CREATE POLICY memory_units_owner_all ON amu.memory_units
    FOR ALL
    TO CURRENT_USER
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS memory_units_select ON amu.memory_units;
CREATE POLICY memory_units_select ON amu.memory_units
    FOR SELECT
    USING (
        lineage_status = 'resolved'
        AND sensitive_columns <@ amu.permitted_columns()
    );

DROP POLICY IF EXISTS memory_units_writer_insert ON amu.memory_units;
CREATE POLICY memory_units_writer_insert ON amu.memory_units
    FOR INSERT
    TO amu_writer
    WITH CHECK (true);

DROP POLICY IF EXISTS memory_units_writer_select ON amu.memory_units;
CREATE POLICY memory_units_writer_select ON amu.memory_units
    FOR SELECT
    TO amu_writer
    USING (true);

-- UPDATE/DELETE: needed for upsert-by-external_id (langchain-amu's
-- add_documents(..., ids=[...]) mutation/idempotency contract) and
-- explicit delete(ids=[...]). Still writer-only -- agents never get these.
DROP POLICY IF EXISTS memory_units_writer_update ON amu.memory_units;
CREATE POLICY memory_units_writer_update ON amu.memory_units
    FOR UPDATE
    TO amu_writer
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS memory_units_writer_delete ON amu.memory_units;
CREATE POLICY memory_units_writer_delete ON amu.memory_units
    FOR DELETE
    TO amu_writer
    USING (true);

-- =====================================================================
-- 8. amu.search(): SECURITY INVOKER so RLS applies under the caller's own
--    role. hnsw.iterative_scan=relaxed_order keeps HNSW returning k rows
--    even when most rows are hidden by the policy above.
-- =====================================================================

-- DROP first: CREATE OR REPLACE can't change a function's RETURNS TABLE
-- column set, and external_id was added to the output after the initial
-- release -- this keeps upgrading an already-installed database idempotent.
DROP FUNCTION IF EXISTS amu.search(vector, int, text);

CREATE FUNCTION amu.search(
    p_query_embedding vector,
    p_k int,
    p_metric_name text DEFAULT NULL
)
RETURNS TABLE (
    id               uuid,
    external_id      text,
    metric_name      text,
    description      text,
    value            jsonb,
    owner_department text,
    definition_hash  text,
    distance         float8
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = amu, public, extensions, pg_catalog
SET hnsw.iterative_scan = 'relaxed_order'
AS $$
    SELECT m.id, m.external_id, m.metric_name, m.description, m.value, m.owner_department,
           m.definition_hash, m.embedding <=> p_query_embedding AS distance
    FROM amu.memory_units m
    WHERE p_metric_name IS NULL OR m.metric_name = p_metric_name
    ORDER BY m.embedding <=> p_query_embedding
    LIMIT p_k
$$;

-- =====================================================================
-- 9. amu.check_conflict(): SECURITY DEFINER, cross-department by nature.
--    Returns ONLY (department, hash) -- never value/lineage/description --
--    so conflict detection can't become a side channel around the gate.
-- =====================================================================

CREATE OR REPLACE FUNCTION amu.check_conflict(
    p_metric_name text,
    p_definition_hash text,
    p_owner_department text
)
RETURNS TABLE (conflicting_department text, other_hash text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = amu, pg_catalog
AS $$
    SELECT DISTINCT m.owner_department, m.definition_hash
    FROM amu.memory_units m
    WHERE m.metric_name = p_metric_name
      AND m.lineage_status = 'resolved'
      AND m.owner_department <> p_owner_department
      AND m.definition_hash <> p_definition_hash
$$;

-- =====================================================================
-- 10. Function-execution grants (roles created in section 6, functions
--     defined above).
-- =====================================================================

GRANT EXECUTE ON FUNCTION amu.search(vector, int, text) TO amu_agent_base, amu_writer;
GRANT EXECUTE ON FUNCTION amu.permitted_columns() TO amu_agent_base, amu_writer;
GRANT EXECUTE ON FUNCTION amu.check_conflict(text, text, text) TO amu_agent_base, amu_writer;

-- amu_writer and amu_agent_base must NEVER be able to read or write the
-- governance metadata tables directly -- only through the SECURITY
-- DEFINER functions above. No GRANTs on sensitive_columns,
-- department_permissions, role_departments, materialization_edges,
-- install_settings for these roles is intentional, not an oversight.
