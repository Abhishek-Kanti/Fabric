# ==========================================
# Stage 1: Build & Dependency Installation
# ==========================================
FROM python:3.12-slim-bookworm AS builder

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

# Compile bytecode during installation
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Copy dependency specifications first to leverage Docker layer caching
COPY pyproject.toml uv.lock README.md ./

# Install production dependencies only into virtual environment
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code
COPY app ./app

# Install the application itself
RUN uv sync --frozen --no-dev


# ==========================================
# Stage 2: Minimal Production Runtime
# ==========================================
FROM python:3.12-slim-bookworm AS runner

# Python runtime optimizations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Create a non-privileged user and group for security
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

# Copy virtual environment and app from builder
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appgroup /app/app /app/app

# Switch to non-root user
USER appuser

# Expose backend port
EXPOSE 8000

# Container healthcheck using built-in urllib (no curl dependency needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

# Start Uvicorn ASGI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
