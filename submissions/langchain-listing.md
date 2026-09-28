# LangChain integration listing -- draft, NOT submitted

Draft answers for the `langchain-ai/docs` "Integration listing" issue form
(`.github/ISSUE_TEMPLATE/06-integration-submission.yml`, fetched live on
2026-09-28). Per that form's own instructions, filing requires the package
to already be published on PyPI -- **as of 2026-09-28, `langchain-amu`
0.1.1 is live at https://pypi.org/project/langchain-amu/**, so that
prerequisite is satisfied. This issue has still not been filed -- that's
release-checklist.md step 9, a separate, not-yet-authorized action. A
maintainer generates the actual docs PR from these fields via automation,
not from a manual PR.

To file for real: open a new issue at
`https://github.com/langchain-ai/docs/issues/new?template=06-integration-submission.yml`
and paste in the fields below.

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
  `langchain-amu` 0.1.1 is live on PyPI as of 2026-09-28 -- check this box
  for real when actually filing.

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
