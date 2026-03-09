# Pin base image to specific version tag for reproducible builds
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .


# --- Final stage ---
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy default configs
COPY config/ /app/config/

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080
ENV CONFIG_DIR=/app/config

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('localhost', 8080), 5); s.close()" || exit 1

CMD ["python", "-m", "odoo_mcp_gateway"]
