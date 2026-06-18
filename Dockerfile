FROM python:3.13-slim

WORKDIR /app

# Install deps
COPY pyproject.toml ./
COPY linkedin_mcp/ ./linkedin_mcp/
COPY README.md LICENSE ./

RUN pip install --no-cache-dir -e .

# Persistent data dir
RUN mkdir -p /app/data
VOLUME /app/data

# Health check
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "from linkedin_mcp.cli import health; import sys; sys.exit(health())" || exit 1

# Default: stdio transport for MCP
ENTRYPOINT ["python3", "-m", "linkedin_mcp.server"]
