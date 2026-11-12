# syntax=docker/dockerfile:1.7
#
# Production image for ECS Fargate on Graviton (linux/arm64).
#
#   docker buildx build --platform linux/arm64 -t tier1-agent .
#
# - Base pinned by tag *and* digest (multi-arch index), so rebuilds are reproducible.
#   Update both together, e.g. via Dependabot/Renovate.
# - Dependencies come from the requirements.txt lock, binary wheels only
#   (no compiler in either stage).
# - Runs as an unprivileged numeric UID. Bytecode is compiled at build time, and
#   the app writes nothing under /app or /opt/venv (readonlyRootFilesystem is an
#   opt-in Terraform toggle; verify it once on Fargate before enforcing it).

ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# ---- Stage 1: build a self-contained virtualenv --------------------------------
FROM --platform=linux/arm64 ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --only-binary=:all: --no-compile -r requirements.txt \
 && python -m compileall -q /opt/venv/lib

# ---- Stage 2: runtime -----------------------------------------------------------
FROM --platform=linux/arm64 ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="tier1-helpdesk-agent" \
      org.opencontainers.image.description="Multi-tenant Tier 1 IT help desk agent (FastAPI + Claude)" \
      org.opencontainers.image.source="https://github.com/cameronksh4-byte/EACOS"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    WEB_CONCURRENCY=2

# Non-root user with a fixed UID/GID (ECS and K8s policies can match on it).
RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid agent --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin agent

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root ./app /app/app
RUN python -m compileall -q /app/app

USER 10001:10001
EXPOSE 8000

# ECS also health-checks through the ALB. This catches a wedged process sooner.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# WEB_CONCURRENCY sets the worker count (uvicorn reads it). The graceful timeout
# stays under the ECS stopTimeout (30s) so in-flight tickets finish on deploys.
# Access logs are off: the ALB keeps request logs, and health checks would flood
# CloudWatch.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--loop", "uvloop", "--http", "httptools", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "25", "--no-access-log"]
