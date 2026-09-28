# Threat model

This describes what amu-pgvector protects against, what it deliberately
does not, and the difference between the two identity modes described in
the README. It assumes familiarity with the paper (Sangaraju & Vissa, IEEE
Access, DOI 10.1109/ACCESS.2026.3730363) and with `docs/design.md`.

## What's protected: derivation-leakage through cached results

The core property: an agent may reuse a cached analytical result (an
Analytical Memory Unit, AMU) only if every sensitive column touched by that
result's derivation is in the requester's permitted set --
`S(a) ⊆ P(d)`. This is enforced by Postgres row-level security on
`amu.memory_units`, not by a filter the application remembers to add. A
request that would otherwise leak a column a department isn't permitted to
see -- because some *other* department cached a result derived from it --
is blocked at the database level, regardless of which client, ORM,
LangChain chain, MCP client, or ad hoc `psql` session is asking.

This is what the leak-rate benchmark (`results/leak_rate/`) measures
directly: a naive content-gated memory (keyed on metric name only, no
lineage check -- representative of existing governed-memory systems that
gate on access tags rather than derivation) leaks roughly half of
cross-department requests in the synthetic workload; amu-pgvector's RLS
gate leaks none, measured over the same requests.

## What's out of scope

**A lying writer.** The SQL gate trusts `amu_writer`'s lineage. Lineage is
extracted from the *executed* SQL via `amu_governance.sql_lineage`, not
self-reported by an agent, which closes the most obvious way to lie -- an
agent can't just claim "this didn't touch anything sensitive." But the
writer role (the trusted interception layer that runs agent SQL and
records the result) is, definitionally, trusted: if that layer is
compromised, or its lineage-extraction step is bypassed, or someone
inserts directly through a connection with `amu_writer` privileges using
fabricated `lineage` jsonb, the row-level trigger will compute
`sensitive_columns` from whatever `lineage` it's given, correctly, but the
input itself would be false. Protecting the writer's own execution path
(sandboxing the agent's SQL execution, verifying the SQL that produced a
given result actually ran) is a different, complementary problem this
project doesn't attempt to solve.

**Inference by combining several permitted AMUs.** The gate is per-AMU:
each retrieval is checked independently against `S(a) ⊆ P(d)`. Nothing
here detects or prevents a requester from combining multiple *individually
permitted* AMUs to infer something they wouldn't be permitted to derive
directly (e.g. two safe aggregates that, combined, narrow down a sensitive
value). This is a general property of column-level access control, not
specific to this system, and is out of scope.

**Superusers, and the table owner under normal (non-FORCE) circumstances.**
Postgres superusers always bypass row-level security, full stop -- there is
no RLS configuration that changes this. `sql/amu_pgvector.sql` uses `FORCE
ROW LEVEL SECURITY` specifically so that the *table owner* is also subject
to the gate (see `docs/design.md`'s `CURRENT_USER`-at-install-time note),
closing one common footgun, but a superuser connection (which a managed
Postgres provider's own admin tooling may use) is never gated by RLS. If
your threat model includes an untrusted superuser, RLS on this table isn't
the control that helps you.

**Side channels in timing.** `amu.search()`, `amu.check_conflict()`, and
the RLS policy itself don't attempt constant-time behavior. A sufficiently
motivated attacker who can measure query latency precisely might be able
to infer *something* about the shape of data they can't see (e.g. whether
a blocked row exists at all, from a timing difference between "no match"
and "match blocked by RLS"). This project makes no claims about resistance
to timing side channels.

**Semantic near-duplicate detection is explicitly experimental, when
built.** If `amu.semantic_conflicts()` (flagging AMUs whose descriptions
are near-duplicates by embedding but whose `definition_hash` differs) ships
at all, it's clearly labeled beyond the paper's scope and beyond this
threat model -- it's a heuristic over embeddings, not a governance
guarantee.

## Identity: strong mode vs. convenience mode

**Strong mode (default).** Each agent or department connects as its own
Postgres role. Department membership comes from `amu.role_departments`,
looked up by `session_user` -- not `current_user`, and not anything set via
`SET` on the connection. `session_user` is fixed for the life of a
connection at authentication time; an agent cannot change which row of
`role_departments` applies to it by executing SQL. This is what
`test_rls_security.py::test_set_department_has_no_effect_in_strong_mode`
verifies directly: `SET amu.department = 'Finance'` from a Marketing-mapped
role has zero effect on `amu.permitted_columns()`'s result in this mode.

**Convenience mode (off by default, install-time opt-in via
`-v convenience_mode_enabled=on`).** A single pooled Postgres role is
shared across many logical departments/agents, and *trusted application
code* sets the department per request with `SET LOCAL amu.department =
'...'` inside a transaction. This exists because some deployments pool
connections and can't give every agent its own Postgres role. **The
security of this mode depends entirely on only trusted code being able to
execute that `SET LOCAL`** -- if an agent's own generated SQL, or anything
it influences, can reach that connection before the trusted code sets (or
after it resets) the department, that agent can claim any department's
permissions. Convenience mode is only as strong as the boundary between
"trusted application code" and "agent-influenced code" on that connection.
Never enable it for a connection the agent's own SQL execution can reach.

## Summary table

| Threat | Status |
|---|---|
| Cross-department leakage of a cached result's derivation columns | **Protected** -- Postgres RLS, `S(a) ⊆ P(d)` |
| Silent metric-definition conflicts (same name, different derivation) | **Protected** -- `amu.check_conflict()`, department+hash only |
| Reclassifying a column as sensitive after AMUs already exist | **Protected** -- reclassification trigger, no rewrite needed |
| Lineage closure silently truncating a long/cyclic derivation chain | **Protected** -- fails closed (unresolved), never truncates |
| A writer that lies about lineage | **Out of scope** -- the writer is trusted by design |
| Inference by combining multiple permitted AMUs | **Out of scope** |
| Postgres superusers / the owner without FORCE RLS | **Out of scope** -- always bypass RLS |
| Timing side channels | **Out of scope** -- no constant-time guarantees |
| Convenience mode misused by agent-reachable code | **Out of scope by design** -- documented as unsafe in that configuration |
