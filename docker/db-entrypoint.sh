#!/bin/sh
# Copies this container's memory figure (anonymous + shared memory, in KB) to a file the app can read,
# so Settings -> Memory can show the database's real use without any access to Docker.
(
  while true; do
    if [ -r /sys/fs/cgroup/memory.stat ]; then
      awk '/^(anon|shmem) /{s+=$2} END{printf "%d", s/1024}' /sys/fs/cgroup/memory.stat > /shared/db_mem_kb.tmp 2>/dev/null \
        && mv /shared/db_mem_kb.tmp /shared/db_mem_kb
    fi
    sleep 30
  done
) &
exec docker-entrypoint.sh "$@"
