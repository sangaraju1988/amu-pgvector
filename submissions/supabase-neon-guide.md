# Lineage-gated agent memory with pgvector and Row-Level Security

*Draft tutorial for Supabase and Neon. Not yet published anywhere.*

**What's actually verified, and what isn't.** Every SQL statement below,
and the whole schema it installs, was tested repeatedly against plain
Postgres 16/17/18 -- both locally (docker-compose,
`pgvector/pgvector:pg17`) and in CI, including specifically as a
non-superuser database owner, which is what both Supabase's and Neon's
default connection roles actually are. What's **not** tested is anything
Supabase- or Neon-specific: their dashboard navigation, their exact
connection-string format, and whether `extensions`-schema pgvector
(Supabase's convention) behaves identically to `public`-schema pgvector
(Neon's default) under load. The dashboard steps below reflect each
platform's own documentation as of 2026-09-28, not firsthand testing on a
live project. If a screenshot or click-path is stale by the time you read
this, trust the platform's current docs over this guide's navigation
instructions -- the SQL itself is the part that's actually verified.

## Why derivation, not content, needs to gate agent memory

If you're building shared memory for AI agents -- a cache of "we already
computed this, don't ask the database again" -- the obvious approach is to
gate retrieval on who's asking and what the cached thing is *labeled* as.
That's what most governed-memory setups do today: tag a cached result with
an access level or a content category, and check the tag at read time.

It misses a specific, easy-to-hit failure mode: a result can be
*derived from* something a requester isn't allowed to see, without
*looking like* that thing at all. An average income figure is just a
number. If your gate only checks "is this number tagged sensitive," and
nobody thought to tag *derived aggregates* the same way as the raw column
they came from, the number leaks. What actually needs to be checked is not
the result's content, but its lineage: which source columns were touched
to produce it, and whether the requester is permitted to see *those*.

This tutorial builds that gate directly in Postgres, using row-level
security (RLS), so it applies universally to every client that queries the
table -- not just the one application that remembered to add a filter.

## What you'll need

- A Supabase project or a Neon project, either with an empty database you
  can install into.
- `psql`, or any Postgres client that can run a `.sql` file against your
  project's connection string.
- Python 3.10+ if you want to follow the client-side part at the end
  (optional -- the gate itself is pure SQL and works from any language).

## Step 1: get pgvector installed

**Supabase.** pgvector isn't pre-installed. In the SQL Editor (Database →
SQL Editor in the dashboard), run:

```sql
create extension if not exists vector with schema extensions;
```

Supabase installs it into the `extensions` schema, not `public`. Keep that
in mind if you ever reference the `vector` type from other SQL you write
by hand -- schema-qualify it (`extensions.vector`) or make sure
`extensions` is on your `search_path`.

**Neon.** Also not pre-installed, and available on every plan at no extra
cost. In the Neon SQL Editor, or via `psql` connected to your branch, run:

```sql
create extension if not exists vector;
```

Neon installs it into `public` by default.

amu-pgvector's install script (`sql/amu_pgvector.sql`) already handles
both locations -- it sets `search_path` to check `public` and
`extensions` before creating its own schema, so you don't need to do
anything differently between the two platforms here.

## Step 2: get your connection string

**Supabase**: Dashboard → your project → **Connect** button → direct
connection. It looks like:

```
postgresql://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
```

**Neon**: Console → your project → **Connect** button → **Connect to your
branch**. It looks like:

```
postgresql://[role]:[password]@[endpoint]-pooler.[region].aws.neon.tech/[dbname]?sslmode=require&channel_binding=require
```

Either way, you want the **direct** (non-pooled/non-transaction-mode)
connection for the install step below -- pooled connections in transaction
mode can interfere with the session-level `SET` commands the install
script uses.

Neither platform's default role is a Postgres superuser, which is exactly
what this project targets: `sql/amu_pgvector.sql` only ever needs
`CREATEROLE` and ordinary object-creation privileges on the database it's
installed into, both of which the default role on each platform already
has.

## Step 3: install the schema

Clone the repo (or just grab `sql/amu_pgvector.sql`) and run:

```bash
psql "<your connection string>" \
  -v embedding_dim=1536 \
  -v convenience_mode_enabled=off \
  -f sql/amu_pgvector.sql
```

`embedding_dim` should match whatever embedding model you plan to use
(1536 is the default, matching common OpenAI-style embedding sizes; use
384 or 768 if you're on a smaller sentence-transformers model). This is
fixed at install time -- changing it later means rebuilding the vector
column and its index.

The script is idempotent: if you run it again (say, after pulling a
newer version of the repo with schema changes), it upgrades in place
rather than erroring on "already exists."

## Step 4: register your governance policy

Everything from here is plain SQL against the `amu` schema the install
script created. Say you have a `customers` table with an `income` column
only your Finance team should be able to derive anything from:

```sql
insert into amu.sensitive_columns (column_id) values ('income');

insert into amu.department_permissions (department, column_id)
values ('Finance', 'income');

-- A role per department. In production you'd typically have one login
-- role per *agent*, each mapped to a department -- see the main README's
-- quickstart and docs/sql-reference.md for the full role model.
create role finance_agent login password 'change-me';
create role marketing_agent login password 'change-me';

insert into amu.role_departments (role_name, department) values
  ('finance_agent', 'Finance'),
  ('marketing_agent', 'Marketing');
```

Writes to `amu.memory_units` should go through a role that's a member of
`amu_writer` (a trusted layer that extracts lineage from the SQL it
actually ran, rather than trusting an agent's self-report):

```sql
create role app_writer login password 'change-me' in role amu_writer;
```

## Step 5: write and read, and watch the gate work

As `app_writer`, insert a cached result with its derivation lineage:

```sql
set role app_writer;

insert into amu.memory_units
  (metric_name, description, value, owner_department, epoch, lineage, definition_hash, embedding)
values (
  'avg_income',
  'average customer income',
  '{"avg": 82000}'::jsonb,
  'Finance',
  1,
  '{"steps":[{"table":"customers","columns_used":["income","customer_id"]}],"filter_logic":"avg(income)"}'::jsonb,
  '0123456789ab',
  -- a real embedding of the description, from whatever model you use
  array_fill(0.1, array[1536])::vector
);

reset role;
```

Now query as each role and compare:

```sql
set role finance_agent;
select metric_name from amu.memory_units;  -- returns avg_income
reset role;

set role marketing_agent;
select metric_name from amu.memory_units;  -- returns nothing
reset role;
```

Nobody wrote application code to filter that second query. Postgres itself
refused to return the row, because `marketing_agent` maps to the
`Marketing` department, and `Marketing` was never granted `income` in
`amu.department_permissions`. That's the whole mechanism: row-level
security checking `S(a) ⊆ P(d)` -- the sensitive columns actually touched
by a cached result's derivation, checked against what the requesting
role's department is permitted to see -- on every single read, regardless
of which client asks.

## Using it from Python

```python
from amu_pgvector import AMUStore
from amu_pgvector.embeddings import fake_embedder  # or a real model

embed = fake_embedder(dim=1536)
writer = AMUStore("postgresql://app_writer:change-me@<host>/<db>")

writer.record(
    "SELECT avg(income) FROM customers",
    {"avg": 82000},
    metric_name="avg_income",
    description="average customer income",
    owner_department="Finance",
    embed_fn=embed,
)

marketing = AMUStore("postgresql://marketing_agent:change-me@<host>/<db>")
marketing.search("average customer income", k=5, embed_fn=embed)  # -> []
```

`amu_pgvector.record()` extracts lineage from the SQL you give it via
[amu-governance](https://github.com/sangaraju1988/amu-governance)'s
`sql_lineage` module -- the same library the underlying paper's reference
implementation uses -- so the lineage stored is derived from the query's
actual AST, not from anything the caller asserts about what it touched.

## Where to go next

- The main [README](https://github.com/sangaraju1988/amu-pgvector) has the
  full quickstart, a diagram of the gate, and real benchmark numbers
  (leak rate, latency, recall) from runs against real Postgres.
- [`docs/threat-model.md`](https://github.com/sangaraju1988/amu-pgvector/blob/main/docs/threat-model.md)
  covers what this does and doesn't protect against -- notably, it trusts
  the writer role's lineage extraction, and it doesn't defend against
  inference by combining multiple individually-permitted results.
- [`docs/sql-reference.md`](https://github.com/sangaraju1988/amu-pgvector/blob/main/docs/sql-reference.md)
  documents every table, function, role and policy the install script
  creates, if you want to understand or extend the schema itself.

This project makes no claim of endorsement by Supabase, Neon, or the
pgvector maintainers -- it's a reference implementation that happens to
target managed Postgres as a first-class deployment target, tested against
what their own documentation says about non-superuser roles and pgvector
installation, not against a live project on either platform.
