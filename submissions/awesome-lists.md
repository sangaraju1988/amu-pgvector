# Curated (awesome-*) list entries -- draft, NOT submitted

Both lists confirmed to exist and fit as of 2026-09-28 (fetched their live
README section structure via `raw.githubusercontent.com`, not searched or
assumed). Each entry below is written to match the target list's own
existing entry format/tone. File a PR to each repo separately, after the
repo is public and (for the MCP one) the server is actually usable.

## punkpeye/awesome-mcp-servers

`https://github.com/punkpeye/awesome-mcp-servers`

Section: **Knowledge & Memory** (`### 🧠 <a name="knowledge--memory"></a>Knowledge & Memory`).
"Security" is a plausible alternate section; "Knowledge & Memory" fits
better since the server's actual function is agent memory retrieval, with
governance as the differentiator, not a general security tool.

Entry (matches the list's `- [Name](url) - description` format):

```markdown
- [amu-pgvector](https://github.com/sangaraju1988/amu-pgvector) 🐍 ☁️ - Lineage-gated agent memory on PostgreSQL + pgvector: a cached analytical result is only served back if every sensitive column touched by its derivation is in the requester's permitted set, enforced by Postgres row-level security rather than application code.
```

(🐍 = Python per the list's own legend; ☁️ = cloud service, since it needs a
Postgres instance -- confirm the legend's exact icon set hasn't changed
before filing, since it's list-maintainer-defined.)

## dhamaniasad/awesome-postgres

`https://github.com/dhamaniasad/awesome-postgres`

Section: **Security** (`### Security`).

Entry (matches the list's `- [Name](url) - description.` format):

```markdown
- [amu-pgvector](https://github.com/sangaraju1988/amu-pgvector) - Row-level security schema and client for lineage-gated AI agent memory: blocks cross-department leakage of cached analytical results derived from sensitive columns, built on pgvector.
```

## Notes

- Neither entry claims endorsement by the list's maintainers, Supabase,
  Neon, or the pgvector maintainers.
- File these only once the repo is public (per `release-checklist.md`) --
  a link to a private repo in a public curated list is useless and will
  likely just get the PR closed.
- Both lists' own contribution guidelines should be re-read at filing time
  in case format requirements changed since 2026-09-28.
