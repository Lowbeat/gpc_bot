#!/bin/bash
set -e

cd /root/gpc_bot

echo "=== Deploying gpc_bot ==="

# Pull latest code
git pull

# Build image
docker build -t gpc_bot .

# Stop and remove old container
docker rm -f gpc_bot 2>/dev/null || true

# Run new container with all necessary settings
docker run -d \
  --name gpc_bot \
  --restart unless-stopped \
  --stop-timeout 60 \
  --env-file .env \
  -e PLAYWRIGHT_HEADLESS=true \
  -e TZ=Europe/Moscow \
  -v /root/gpc_bot/Static\ content:/app/Static\ content:ro \
  gpc_bot

# Show logs
docker logs -f gpc_bot