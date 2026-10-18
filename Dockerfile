# Container image for the amu-pgvector MCP server (stdio transport).
#
# The server connects to Postgres lazily, on the first tool call, so it starts
# and answers MCP introspection (initialize, tools/list) without a database.
# To run real queries, pass the DSN of the Postgres role it should run as:
#
#   docker build -t amu-pgvector-mcp .
#   docker run -i --rm -e AMU_PGVECTOR_DSN=postgresql://user:pass@host:5432/db amu-pgvector-mcp
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY packages/amu-pgvector /app/packages/amu-pgvector
RUN pip install "/app/packages/amu-pgvector[mcp]"

RUN useradd --create-home --uid 10001 amu
USER amu

ENTRYPOINT ["amu-pgvector-mcp"]
