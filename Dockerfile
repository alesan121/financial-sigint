# =============================================================================
# Multi-Stage Dockerfile - Financial SIGINT Ingress (ADC)
#
# Hardware analogy: a two-phase manufacturing process.
# Stage 1 (builder): The "assembly line" — installs build tools, compiles
#                     dependencies, and exports them as clean components.
# Stage 2 (runtime):  The "final board" — contains only what's strictly
#                     necessary to run. No build tools, no "debug chips."
#                     Result: a minimal, secure, efficient image.
# =============================================================================

# =============================================================================
# STAGE 1: Builder
# Installs Poetry, resolves dependencies and exports them to requirements.txt.
# This stage is discarded at the end; only its output (installed deps) persists.
# =============================================================================
FROM python:3.14-slim AS builder

# Image metadata
LABEL maintainer="Financial SIGINT Team"
LABEL stage="builder"

# Environment variables for Poetry and Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Install Poetry via pip (the most robust and deterministic approach)
RUN pip install "poetry==$POETRY_VERSION"

WORKDIR /build

# Copy only the dependency manifest files first.
# This takes advantage of Docker's layer cache: if pyproject.toml doesn't
# change, this layer is reused and dependencies aren't reinstalled every build.
COPY pyproject.toml poetry.lock* ./

# Export production dependencies to requirements.txt
# --without-hashes: more compatible across different environments
# --only main: excludes dev dependencies (ruff, black, pytest, etc.)
RUN poetry export --format=requirements.txt --output=requirements.txt --only=main --without-hashes

# =============================================================================
# STAGE 2: Runtime
# Minimal final image: only Python + production dependencies + source code.
# No Poetry, no build tools, no test files.
# =============================================================================
FROM python:3.14-slim AS runtime

LABEL maintainer="Financial SIGINT Team"
LABEL version="0.1.0"
LABEL description="Financial SIGINT - Ingress Microservice (ADC)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install system dependencies needed at runtime
# (libstdc++ for some Python libs compiled with C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements exported from the builder stage
COPY --from=builder /build/requirements.txt .

# Install production dependencies and the L1 Vector Engine
RUN pip install --no-cache-dir -r requirements.txt fastembed onnxruntime numpy

# Copy the microservice source code
COPY src/ ./src/

# SECURITY: Create and use a non-root user.
# Principle of least privilege: the app process doesn't need to be root.
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash appuser && \
    mkdir -p /data /home/appuser/.cache/py-yfinance && \
    chown -R appuser:appgroup /app /data /home/appuser/.cache

USER appuser

# This is a single image shared by every service in docker-compose.yml (the
# six scouts, the orchestrator gateway, the dashboard, the position monitor
# and the Telegram bot) — each one overrides CMD with its own `command:`.
# The values below are only the default when the image is run standalone
# (e.g. `docker run financial-sigint`), which starts the orchestrator gateway.

# Exposed port (documentation only; the actual mapping happens in docker-compose or k8s)
EXPOSE 8001

# Health check built into the image for Docker/Kubernetes.
# Every docker-compose service overrides this with its own, more specific check.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/docs || exit 1

CMD ["python", "-m", "orchestrator.workers.main"]
