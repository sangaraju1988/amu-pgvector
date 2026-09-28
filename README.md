# amu-pgvector

Reference implementation of **Lineage-Aware Memory Governance**
(Sangaraju & Vissa, *IEEE Access*, [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363))
on PostgreSQL + [pgvector](https://github.com/pgvector/pgvector).

An agent may reuse a cached analytical result (an Analytical Memory Unit,
AMU) only if every sensitive column in that result's derivation lineage is in
the requester's permitted set: `S(a) ⊆ P(d)`. Here, **Postgres enforces the
gate itself through row-level security** — it is not a filter the application
has to remember to add.

> **Status:** under active development (Phase 1/6 — scaffold). The
> quickstart, security model, and benchmark numbers below will be filled in
> as each phase lands; nothing in this README is a claim yet. See
> [`docs/design.md`](docs/design.md) for the current design notes.

## License

MIT. See [`LICENSE`](LICENSE).

## Related

- Paper: Sangaraju & Vissa, *IEEE Access*, DOI [10.1109/ACCESS.2026.3730363](https://doi.org/10.1109/ACCESS.2026.3730363)
- Reference library: [amu-governance](https://github.com/sangaraju1988/amu-governance) ([PyPI](https://pypi.org/project/amu-governance/))
