-- Reverses sql/amu_pgvector.sql. Safe to run even if the install was
-- partial or already removed (IF EXISTS everywhere). Does NOT drop the
-- `vector` extension itself -- something else in the database may depend
-- on it, and managed Postgres providers usually own its lifecycle anyway.
--
-- Per-agent/per-department LOGIN roles you created via admin helpers (not
-- part of the base install) are left alone -- drop those yourself if you
-- want them gone too.

DROP SCHEMA IF EXISTS amu CASCADE;

DROP ROLE IF EXISTS amu_writer;
DROP ROLE IF EXISTS amu_agent_base;
