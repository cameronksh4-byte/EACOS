# syntax=docker/dockerfile:1
# Multi-stage ARM64 image for ECS Fargate (Graviton).
# Build:  docker buildx build --platform linux/arm64 -t tier1-agent .

# Stage 1: Builder - resolve and build every wheel, including dependencies.
FROM --platform=linux/arm64 python:3.12-slim AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip wheel --wheel-dir /build/wheels -r requirements.txt

# Stage 2: Runtime
FROM --platform=linux/arm64 python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Create non-root user for zero-trust container security
RUN groupadd -r agentgroup && useradd -r -g agentgroup -d /app -s /usr/sbin/nologin agentuser

COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

COPY --chown=agentuser:agentgroup ./app /app/app

USER agentuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
