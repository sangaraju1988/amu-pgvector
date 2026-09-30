# MCP Registry publishing

**Re-synced 2026-09-30, current version 0.1.3**:
`io.github.sangaraju1988/amu-pgvector` is live, `status: active`,
`isLatest: true` at 0.1.3 (0.1.1 and 0.1.2 both stay listed as prior
history, not `isLatest`, which is correct registry behavior -- versions
aren't overwritten) --
https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.sangaraju1988/amu-pgvector
-- verified via that API directly, not just the CLI's own success message.

The saved JWT had expired again by the time this re-sync was done (a day
after the *previous* re-login, not just "hours" as noted below) -- this
really does expire between essentially any two sessions with real time
between them; always attempt `publish` first and only re-login on a 401,
never assume a prior login is still valid.

Republishing an updated version needs a **fresh** `mcp-publisher login
github` first -- the saved JWT expires (hit this directly: a `publish`
run after some hours failed with `401: token is expired`, fixed with a
plain re-login, no other change needed).

Two real, live-API-caught issues fixed along the way (both now fixed on
`main`, commits `6eeb741` and `878df49` -- `server.json` at the repo root
reflects the working, published configuration):

1. **`description` exceeded the registry's 100-character limit.**
   `mcp-publisher validate` caught this before any publish attempt
   (`422: expected length <= 100 for body.description`). Shortened from
   165 to 88 characters, same meaning.
2. **`identifier` cannot include a PyPI extras suffix.**
   `mcp-publisher publish` rejected `"amu-pgvector[mcp]"` with
   `PyPI package 'amu-pgvector[mcp]' not found (status: 404)` -- the
   registry's ownership check does a literal PyPI JSON API lookup on
   `identifier`, and extras (`[mcp]`) are an install-time pip concept, not
   part of a package's actual registry name. Fixed by reverting
   `identifier` to the bare `"amu-pgvector"` and expressing the real
   required invocation via `runtimeHint: "uvx"` +
   `runtimeArguments: [{name: "--from", value: "amu-pgvector[mcp]==0.1.1"}]`
   + `packageArguments: [{value: "amu-pgvector-mcp"}]` instead -- composing
   to `uvx --from amu-pgvector[mcp]==0.1.1 amu-pgvector-mcp`, which
   correctly installs the extra the console script's `mcp` SDK dependency
   needs (kept optional at the base-package level so `AMUStore`/
   `langchain-amu` users don't pull it in unnecessarily).

## What was actually done, for the record

- `packages/amu-pgvector/README.md`'s ownership-verification marker
  (`<!-- mcp-name: io.github.sangaraju1988/amu-pgvector -->`) was already
  live in `amu-pgvector`'s published PyPI description (verified via the
  PyPI JSON API) before this was attempted -- required for the registry's
  PyPI ownership check to pass at all.
- `mcp-publisher` installed via `brew install mcp-publisher` (1.8.1),
  not the raw-binary download this draft originally suggested.
- Authenticated via `mcp-publisher login github` -- GitHub OAuth device
  flow, completed interactively (the user visited the printed URL/code;
  this can't be done by an agent alone).
- `mcp-publisher validate server.json` and `mcp-publisher publish` both
  run for real against the live registry, not simulated.

## Commands, for the next version

```bash
# 1. Install mcp-publisher (brew is simpler than the raw binary download
#    the registry's own quickstart shows -- both work)
brew install mcp-publisher

# 2. Bump version in BOTH places in server.json: the top-level "version"
#    and packages[0].version -- and packages[0].runtimeArguments' --from
#    value ("amu-pgvector[mcp]==<version>"), to match the newly released
#    PyPI version.

# 3. Validate (safe to run any time, no auth needed)
mcp-publisher validate server.json

# 4. Authenticate -- GitHub OAuth device flow, since the server name is
#    under io.github.sangaraju1988/*. Needed again if the saved token
#    has expired; skip if still logged in.
mcp-publisher login github
# Follow the printed URL + device code, authorize interactively --
# an agent cannot complete this step alone.

# 5. Publish (reads server.json from the current directory; the registry
#    URL comes from the saved login token, not a flag)
mcp-publisher publish

# 6. Verify
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.sangaraju1988/amu-pgvector"
```

## Notes

- This listing implies nothing about endorsement by Anthropic, the MCP
  project, or the pgvector maintainers.
- If `mcp-publisher validate` or `publish` reports a schema/field mismatch,
  trust the live error over this file -- the registry's schema evolves
  ("currently in preview") and this draft may be stale by the time it's
  actually run.
