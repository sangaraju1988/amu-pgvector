# LangChain integration listing

**Filed 2026-09-28: https://github.com/langchain-ai/docs/issues/6276**

Re-verified the live issue form (`.github/ISSUE_TEMPLATE/06-integration-
submission.yml`) immediately before filing -- unchanged since it was
first drafted, same day. Filed via `gh issue create` with a body matching
the form's own rendered field structure exactly. Submitter-supplied
`integration-submission`/`integration` labels didn't take (external
contributors can't apply labels on this repo) -- expected, and the form's
own text says a maintainer applies `integration-run` during review anyway.

Fields below are what was actually submitted, kept for reference.

## Form fields

**Display or class name**
```
AMUVectorStore
```

**Language**
```
Python
```

**Component type**
```
vectorstores
```

**PyPI package name**
```
langchain-amu
```

**npm package name**
```
(blank -- Python only)
```

**Docs URL**
```
https://github.com/sangaraju1988/amu-pgvector#readme
```
(No separate partner docs site; the repo README is the integration's
documentation. Update this if a dedicated docs page is published before
filing.)

**Source repository**
```
sangaraju1988/amu-pgvector
```

**Short provider description**
```
Vector store backed by PostgreSQL + pgvector where Postgres row-level
security gates retrieval on a cached result's derivation lineage, not
just its content or access tags.
```

**Capability flags**
```
delete_by_id: true
```
(`get_by_ids`, `add_documents(..., ids=[...])`, and `delete(ids=[...])`
are all implemented -- see `packages/langchain-amu/src/langchain_amu/vectorstore.py`.
Streaming/tool-calling flags don't apply to a vector store. Left
`structured_output` and similar chat-only flags out entirely, per the
form's "omit unknown flags" instruction, rather than guessing.)

**Confirmations**
- [x] The package is already published on PyPI and/or npm.
  `langchain-amu` 0.1.1 was live on PyPI at filing time.

## Notes for whoever files this

- The standard `langchain-tests` `VectorStoreIntegrationTests` suite passes
  8/8 of its sync, non-metadata-echo-dependent tests; 8 are marked
  `xfail(strict=True)` for a documented, structural reason (every
  `Document.metadata` here always carries governance fields --
  `metric_name`/`owner_department`/`definition_hash` -- alongside whatever
  the caller supplied, which conflicts with a handful of the suite's
  exact-metadata-equality assertions). If a reviewer asks about test
  conformance, point them at `packages/langchain-amu/tests/test_standard.py`
  and `docs/design.md`'s "Phase 4 additions" section -- don't claim full
  conformance.
- Nothing in this listing implies Supabase, Neon, LangChain, or the
  pgvector maintainers endorse this project.
