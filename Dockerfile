FROM python:3.12-slim

WORKDIR /app

# Install the package (deps + console script). Copy only what's needed to build,
# so the layer cache survives unrelated changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Default to stdio, the transport an MCP client uses to drive a containerised
# server: `docker run -i --rm pexafy-mcp` speaks MCP on stdin/stdout and answers
# introspection with no key and no network. Defaulting to http instead left that
# invocation hanging — the container listening on a port nobody was talking to —
# which is how an automated directory check sees a broken server.
# Both compose files set PEXAFY_MCP_TRANSPORT=http explicitly, so serving over
# HTTP is unaffected by this default.
#
# FASTMCP_CHECK_FOR_UPDATES=off keeps the container from pinging PyPI on startup
# (the server also forces this in code; set here too for an explicit, auditable image).
ENV PEXAFY_MCP_TRANSPORT=stdio \
    PEXAFY_MCP_HOST=0.0.0.0 \
    PEXAFY_MCP_PORT=8765 \
    FASTMCP_CHECK_FOR_UPDATES=off

EXPOSE 8765

CMD ["pexafy-mcp"]
