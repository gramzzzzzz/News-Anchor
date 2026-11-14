#!/bin/bash
# Restores a backup dump into a throwaway PostgreSQL container (not the live one: its 512 MB limit does not survive a parallel restore).
# Usage: scripts/audit-restore.sh <dump file>     then:  docker exec na-audit-pg psql -U na -d na
set -e
DUMP="$1"
docker rm -f na-audit-pg >/dev/null 2>&1 || true
docker run -d --name na-audit-pg -e POSTGRES_USER=na -e POSTGRES_PASSWORD=na -e POSTGRES_DB=na pgvector/pgvector:pg17 >/dev/null
until docker exec na-audit-pg pg_isready -U na -d na >/dev/null 2>&1; do sleep 1; done
sleep 2
docker cp "$DUMP" na-audit-pg:/tmp/before.dump
docker exec na-audit-pg sh -c 'pg_restore -U na -d na -j 2 --no-owner /tmp/before.dump > /tmp/restore.log 2>&1; echo finished >> /tmp/restore.log'
