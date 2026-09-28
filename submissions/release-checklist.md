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

- [x] **7. Publish both packages via the trusted-publish workflow.** Done 2026-09-28. Bumped both packages to `0.1.1` (commit `813e338` — `v0.1.0`'s tag predated the environment-name fix, so it could never have published), tagged and released `v0.1.1`, which fired `publish.yml`: both `amu-pgvector` and `langchain-amu` jobs succeeded (run `36458896144`). Verified live: `https://pypi.org/project/amu-pgvector/0.1.1/` and `https://pypi.org/project/langchain-amu/0.1.1/`, and confirmed both install and import correctly from a fresh, isolated venv against the real PyPI index (not the local build).

- [ ] **8. Publish to the MCP registry.** Follow `submissions/mcp-registry.md` exactly -- it depends on step 7 (PyPI `amu-pgvector[mcp]` must exist) and on `packages/amu-pgvector/README.md`'s `mcp-name:` marker matching `server.json`'s `name` field, which it already does as of this draft; just re-verify nothing renamed one without the other in the meantime.

- [ ] **9. File the LangChain listing issue.** Follow `submissions/langchain-listing.md`, using the live issue-form template at the time of filing (re-fetch it -- the form's fields may have changed since this draft was written on 2026-09-28). Depends on step 7 (`langchain-amu` must already be on PyPI; the form's confirmation checkbox requires it).

- [ ] **10. (Optional, any time after step 1) File the awesome-list PRs.** `submissions/awesome-lists.md` has both entries drafted. Not order-dependent on the PyPI/MCP/LangChain steps, but do it after the repo is public (step 1) -- a link to a private repo is useless there.

## What this checklist deliberately does not include

Nothing here files, publishes, or tags anything automatically. Every
checkbox is a manual action for the project owner to take, in their own
time, after reviewing what it actually does. This file exists so that when
that time comes, the order and dependencies between steps don't have to be
re-derived from scratch.
