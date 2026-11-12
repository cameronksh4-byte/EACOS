.PHONY: help install lock test test-sql lint image run

IMAGE ?= tier1-agent:local
SCRATCH_DB_URL ?= postgresql://postgres:postgres@localhost:5432/postgres

help:
	@echo "install   install dev dependencies"
	@echo "lock      re-resolve requirements.in -> requirements.txt for linux/arm64 py3.12"
	@echo "test      run the Python test suite (offline, no API spend)"
	@echo "test-sql  apply the migration and run the SQL checks against SCRATCH_DB_URL (never production)"
	@echo "lint      ruff"
	@echo "image     build the production ARM64 image"
	@echo "run       run the API locally with .env"

install:
	pip install -r requirements-dev.txt

lock:
	python scripts/lock_requirements.py

test:
	python -m pytest -q

test-sql:
	psql "$(SCRATCH_DB_URL)" -v ON_ERROR_STOP=1 -q -f supabase/tests/supabase_stub.sql
	psql "$(SCRATCH_DB_URL)" -v ON_ERROR_STOP=1 -q -f supabase/migrations/001_init_schema.sql
	psql "$(SCRATCH_DB_URL)" -v ON_ERROR_STOP=1 -f supabase/tests/001_init_schema_test.sql

lint:
	ruff check --select E,F,B --ignore E501 app ingest tests scripts

image:
	docker buildx build --platform linux/arm64 -t $(IMAGE) --load .

run:
	uvicorn app.main:app --reload --env-file .env
