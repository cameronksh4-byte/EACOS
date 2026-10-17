# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home triage
COPY --from=build /install /usr/local
WORKDIR /app
COPY triage_engine ./triage_engine
USER 10001
EXPOSE 8080
# Read-only root filesystem friendly: nothing is written to disk at runtime.
CMD ["uvicorn", "--factory", "triage_engine.main:create_app", \
     "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", \
     "--no-server-header", "--timeout-keep-alive", "5"]
