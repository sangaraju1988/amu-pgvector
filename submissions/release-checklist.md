# Release checklist -- draft, nothing here has been done

Order matters: several steps depend on an earlier one having actually
completed (PyPI trusted publishing needs the repo public; the MCP registry
publish needs the PyPI release to exist and be readable; the LangChain
listing form requires the package to already be on PyPI). Each step below
is a decision or action the project owner needs to take explicitly --
nothing in this file has been executed, and nothing should be executed
without going through this list in order.

- [ ] **1. Make the repo public.** `gh repo edit sangaraju1988/amu-pgvector --visibility public --accept-visibility-change-consequences` (or via the GitHub UI: Settings → General → Danger Zone → Change visibility). Irreversible in the sense that anything ever pushed becomes visible in history; review the repo one more time first, in particular that no `.env`/credentials/DSNs with real passwords ever got committed (the docker-compose and test credentials in this repo are all fixed, non-secret dev-only values, but double check before flipping this).

- [ ] **2. Configure PyPI trusted publishing for both packages.** On pypi.org, for each of `amu-pgvector` and `langchain-amu`: Account settings → Publishing → add a new pending publisher, GitHub Actions, owner `sangaraju1988`, repo `amu-pgvector`, workflow filename (a new `.github/workflows/publish.yml` needs to be added -- not yet written; the current `ci.yml` only tests/lints), environment name of your choosing. This has to happen before the first trusted-publish run, and needs the repo to already be public (step 1) since PyPI's OIDC trust verifies against the public repo.

- [ ] **3. Tag `v0.1.0`.** `git tag -a v0.1.0 -m "..."` and push the tag. Decide first whether both packages release together at the same version number (simpler) or independently (more flexible, more bookkeeping) -- this repo's `pyproject.toml` files currently both say `0.1.0` with no independent versioning scheme set up.

- [ ] **4. Create the GitHub Release from that tag.** This is what triggers Zenodo's GitHub integration (once connected, see next step) to mint a DOI automatically. Write real release notes -- don't let GitHub's auto-generated commit list stand alone as the release description.

- [ ] **5. Confirm the Zenodo DOI.** Requires the Zenodo GitHub integration to already be enabled for this repo (zenodo.org → GitHub → toggle the repo on) *before* step 4's release is created -- Zenodo archives releases going forward, not retroactively. If the integration wasn't enabled before the v0.1.0 release, either enable it now and do a `v0.1.1` release to get the first archived DOI, or manually upload the v0.1.0 source to Zenodo. Once a DOI exists, update `CITATION.cff` (add a `doi:` field to the software entry, which is currently and deliberately absent since no DOI exists yet) and `.zenodo.json` if any metadata drifted.

- [ ] **6. Update the DOI badge in the README.** Add `[![DOI](https://zenodo.org/badge/DOI/<the-real-doi>.svg)](https://doi.org/<the-real-doi>)` near the top of `README.md`, using the actual DOI from step 5 -- never a placeholder.

- [ ] **7. Run the actual `uv build` + `twine upload` (or the new trusted-publish workflow) for both packages**, in dependency order: `amu-pgvector` first (since `langchain-amu` depends on it by pinned version), then `langchain-amu`. Confirm both install cleanly from PyPI in a fresh venv (`pip install amu-pgvector[mcp,st]` and `pip install langchain-amu`) before moving on.

- [ ] **8. Publish to the MCP registry.** Follow `submissions/mcp-registry.md` exactly -- it depends on step 7 (PyPI `amu-pgvector[mcp]` must exist) and on `packages/amu-pgvector/README.md`'s `mcp-name:` marker matching `server.json`'s `name` field, which it already does as of this draft; just re-verify nothing renamed one without the other in the meantime.

- [ ] **9. File the LangChain listing issue.** Follow `submissions/langchain-listing.md`, using the live issue-form template at the time of filing (re-fetch it -- the form's fields may have changed since this draft was written on 2026-09-28). Depends on step 7 (`langchain-amu` must already be on PyPI; the form's confirmation checkbox requires it).

- [ ] **10. (Optional, any time after step 1) File the awesome-list PRs.** `submissions/awesome-lists.md` has both entries drafted. Not order-dependent on the PyPI/MCP/LangChain steps, but do it after the repo is public (step 1) -- a link to a private repo is useless there.

## What this checklist deliberately does not include

Nothing here files, publishes, or tags anything automatically. Every
checkbox is a manual action for the project owner to take, in their own
time, after reviewing what it actually does. This file exists so that when
that time comes, the order and dependencies between steps don't have to be
re-derived from scratch.
