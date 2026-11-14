#!/bin/bash
# Runs the full test suite (including database tests) against a throwaway PostgreSQL container.
set -e
cd "$(dirname "$0")/../backend"
docker rm -f na-test-pg >/dev/null 2>&1 || true
docker run --rm -d --name na-test-pg -p 55432:5432 -e POSTGRES_USER=na -e POSTGRES_PASSWORD=na -e POSTGRES_DB=na pgvector/pgvector:pg17 >/dev/null
trap 'docker rm -f na-test-pg >/dev/null 2>&1 || true' EXIT
until docker exec na-test-pg pg_isready -U na -d na >/dev/null 2>&1; do sleep 1; done
sleep 2
NA_TEST_DATABASE_URL=postgresql://na:na@localhost:55432/na PYTHONPATH=. .venv/bin/python -m pytest -q "$@"
