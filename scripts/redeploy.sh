#!/bin/bash
# Rebuild and restart the app container (the database container is left alone).
cd "$(dirname "$0")/.."
/opt/homebrew/bin/docker-compose up -d --build app > /tmp/na-up.log 2>&1
echo "exit $?" >> /tmp/na-up.log
