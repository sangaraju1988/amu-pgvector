# MCP Registry publishing -- draft, NOT submitted

Exact `mcp-publisher` commands and the validated `server.json` (at the repo
root) for publishing `amu-pgvector`'s MCP server to the official MCP
Registry (`registry.modelcontextprotocol.io`). Conventions confirmed live
against `github.com/modelcontextprotocol/registry` on 2026-09-28 (the
publisher quickstart and package-types docs) -- re-check before actually
running these if much time has passed, since the registry documents itself
as "currently in preview."

**As of 2026-09-28, `amu-pgvector` 0.1.1 is published on PyPI** (release-
checklist.md step 7) and its live PyPI description contains the
`mcp-name:` marker (verified via the PyPI JSON API) -- the prerequisite
below is satisfied. Registering with the MCP registry itself
(`mcp-publisher publish`) is still a separate, not-yet-authorized action
(release-checklist.md step 8).

## Prerequisites already done in this repo

- `packages/amu-pgvector/README.md` contains the ownership-verification
  marker: `<!-- mcp-name: io.github.sangaraju1988/amu-pgvector -->`. Its
  value matches `server.json`'s `name` field exactly, as required.
- `server.json` at the repo root, `registryType: "pypi"`,
  `identifier: "amu-pgvector[mcp]"` (the `[mcp]` extra is required for the
  `amu-pgvector-mcp` console script to exist -- plain `amu-pgvector` alone
  does not install it).

## Commands, in order

```bash
# 1. Install mcp-publisher (macOS/Linux; see the quickstart for Windows)
curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" \
  | tar xz mcp-publisher
sudo mv mcp-publisher /usr/local/bin/

# 2. Validate server.json against the schema (safe to run any time, no auth needed)
mcp-publisher validate server.json

# 3. Authenticate -- GitHub OAuth device flow, since the server name is
#    under io.github.sangaraju1988/*
mcp-publisher login github
# Follow the printed URL + device code, authorize, wait for "Successfully logged in".

# 4. Publish (reads server.json from the current directory; the registry
#    URL comes from the token login saved, not a flag)
mcp-publisher publish

# 5. Verify
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.sangaraju1988/amu-pgvector"
```

## After publishing a new version later

Bump `version` in both `server.json`'s top level and its one entry in
`packages[].version` to match the newly released PyPI version, then repeat
steps 2 and 4 (re-auth with `mcp-publisher login github` first if the saved
token has expired).

## Notes

- This listing implies nothing about endorsement by Anthropic, the MCP
  project, or the pgvector maintainers.
- If `mcp-publisher validate` or `publish` reports a schema/field mismatch,
  trust the live error over this file -- the registry's schema evolves
  ("currently in preview") and this draft may be stale by the time it's
  actually run.
