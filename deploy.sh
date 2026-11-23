#!/bin/bash
set -e

cd /root/gpc_bot

echo "=== Deploying gpc_bot ==="

# Pull latest code
git pull

# Build and start via docker-compose (uses docker-compose.yml settings)
/c/Program\ Files/Docker/Docker/resources/bin/docker-compose up -d --build

# Show logs
/c/Program\ Files/Docker/Docker/resources/bin/docker-compose logs -f