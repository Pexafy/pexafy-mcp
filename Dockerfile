FROM python:3.12-slim

WORKDIR /app

# Install the package (deps + console script). Copy only what's needed to build,
# so the layer cache survives unrelated changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Streamable HTTP service on 0.0.0.0:8765 (overridable via env).
ENV PEXAFY_MCP_TRANSPORT=http \
    PEXAFY_MCP_HOST=0.0.0.0 \
    PEXAFY_MCP_PORT=8765

EXPOSE 8765

CMD ["pexafy-mcp"]
