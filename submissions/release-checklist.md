# Release checklist

Order matters: several steps depend on an earlier one having actually
completed (PyPI trusted publishing needs the repo public; the MCP registry
publish needs the PyPI release to exist and be readable; the LangChain
listing form requires the package to already be on PyPI). Each step below
is a decision or action the project owner needs to take explicitly.

- [x] **1. Make the repo public.** Done 2026-09-28 via `gh repo edit sangaraju1988/amu-pgvector --visibility public --accept-visibility-change-consequences`.

- [x] **2. Configure PyPI trusted publishing for both packages.** Done 2026-09-28. Two **pending publishers** registered at `pypi.org/manage/account/publishing/` (the project doesn't exist yet, so this is the "pending" flow, not the per-project one):

  | PyPI project | Repo | Workflow | Environment |
  |---|---|---|---|
  | `amu-pgvector` | `sangaraju1988/amu-pgvector` | `publish.yml` | `pypi-amu-pgvector` |
  | `langchain-amu` | `sangaraju1988/amu-pgvector` | `publish.yml` | `pypi-langchain-amu` |

  **Found while doing this**: PyPI keys a trusted-publisher config on
  `(repo, workflow, environment)` → exactly one PyPI project name. The
  first `.github/workflows/publish.yml` draft used the *same* `pypi`
  environment for both jobs, so registering the second pending publisher
  failed with "A pending trusted publisher matching this configuration has
  already been registered for a different project name." Fixed by giving
  each job its own environment (`pypi-amu-pgvector` / `pypi-langchain-amu`,
  commit `cf88ef3`) — if you're publishing more than one PyPI project from
  one workflow file, each job needs a distinct `environment:`, full stop.
  The two matching GitHub Actions environments were created via
  `gh api --method PUT repos/.../environments/<name>` (they don't need any
  protection rules configured — trusted publishing works from a
  bare environment with no reviewers/branch restrictions, though adding
  those is good practice if you want a human gate on publishing later).

- [x] **3. Tag `v0.1.0`.** Done 2026-09-28 (annotated tag, pushed).

- [x] **4. Create the GitHub Release from that tag.** Done 2026-09-28, real release notes (not the auto-generated commit list): https://github.com/sangaraju1988/amu-pgvector/releases/tag/v0.1.0. Publishing it auto-triggered `publish.yml` (it fires on any `release: published`) — that run failed fast and safely with "Trusted publishing exchange failure: invalid-publisher" since step 2 hadn't been done yet at that point; no upload was attempted. Step 2 is now fixed and correctly configured, but the `v0.1.0` tag still points at the *old*, buggy `publish.yml` (the fix landed in a later commit, `cf88ef3`) — **the existing v0.1.0 release's workflow run cannot be successfully re-run as-is**. The next release (or a re-tag) will pick up the fix automatically.

- [ ] **5. Confirm the Zenodo DOI.** Requires the Zenodo GitHub integration to already be enabled for this repo (zenodo.org → GitHub → toggle the repo on) *before* step 4's release is created -- Zenodo archives releases going forward, not retroactively. If the integration wasn't enabled before the v0.1.0 release, either enable it now and do a `v0.1.1` release to get the first archived DOI, or manually upload the v0.1.0 source to Zenodo. Once a DOI exists, update `CITATION.cff` (add a `doi:` field to the software entry, which is currently and deliberately absent since no DOI exists yet) and `.zenodo.json` if any metadata drifted.

- [ ] **6. Update the DOI badge in the README.** Add `[![DOI](https://zenodo.org/badge/DOI/<the-real-doi>.svg)](https://doi.org/<the-real-doi>)` near the top of `README.md`, using the actual DOI from step 5 -- never a placeholder.

- [x] **7. Publish both packages via the trusted-publish workflow.** Done 2026-09-28, current live version **0.1.2**. First published as `0.1.1` (commit `813e338` — `v0.1.0`'s tag predated the environment-name fix, so it could never have published). Then found both PyPI pages were rendering as effectively blank -- `packages/*/README.md` were one-line scaffold stubs, and PyPI renders exactly that file as the package's long description. Fixed with real READMEs and republished as `0.1.2` (a PyPI release's files/metadata can never be overwritten, so this needed a new version, not an edit). Verified live: `https://pypi.org/project/amu-pgvector/0.1.2/` and `https://pypi.org/project/langchain-amu/0.1.2/`, both with real rendered descriptions (4230 / 3046 chars), and confirmed both install and import correctly from a fresh, isolated venv against the real PyPI index with `--refresh` (uv's local index cache can lag a published release by a few minutes -- don't mistake that for a publish failure).

- [x] **8. Publish to the MCP registry.** Done 2026-09-28: `io.github.sangaraju1988/amu-pgvector` v0.1.1 is live, `status: active` — https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.sangaraju1988/amu-pgvector. Two real bugs caught by the live API and fixed (commits `6eeb741`, `878df49`): `description` exceeded the registry's 100-char limit, and `identifier` can't carry a PyPI extras suffix (`amu-pgvector[mcp]` 404'd — fixed via `runtimeHint`/`runtimeArguments` instead, see `submissions/mcp-registry.md`). Authenticated via `mcp-publisher login github`'s device flow, completed by the user interactively.

- [x] **9. File the LangChain listing issue.** Done 2026-09-28: https://github.com/langchain-ai/docs/issues/6276. Re-fetched the live issue-form template immediately before filing (unchanged). Now waiting on a maintainer to review and apply `integration-run` to trigger the docs-PR automation -- nothing further to do on this end unless they ask for changes.

- [ ] **10. (Optional, any time after step 1) File the awesome-list PRs.** `submissions/awesome-lists.md` has both entries drafted. Not order-dependent on the PyPI/MCP/LangChain steps, but do it after the repo is public (step 1) -- a link to a private repo is useless there.

## What this checklist deliberately does not include

Nothing here files, publishes, or tags anything automatically. Every
checkbox is a manual action for the project owner to take, in their own
time, after reviewing what it actually does. This file exists so that when
that time comes, the order and dependencies between steps don't have to be
re-derived from scratch.
