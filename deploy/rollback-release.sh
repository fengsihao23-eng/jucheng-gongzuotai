#!/usr/bin/env bash
# Run on the server with the exact backup directory. Restore application only; keep new business records.
set -euo pipefail
RELEASE_DIR="${1:?Exact /opt/jucheng/deploy/releases/... directory required}"
[[ "$RELEASE_DIR" =~ ^/opt/jucheng/deploy/releases/[0-9]{8}-[0-9]{6}-[a-f0-9]{12}$ ]] || exit 2
test -s "$RELEASE_DIR/previous-image.txt"
test -s "$RELEASE_DIR/previous-revision.txt"
IMAGE="$(<"$RELEASE_DIR/previous-image.txt")"
REVISION="$(<"$RELEASE_DIR/previous-revision.txt")"
[[ "$IMAGE" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || exit 2
cd /opt/jucheng/deploy
# The migration only adds tables; start the previous app without running its older Alembic chain.
docker compose -f docker-compose.yml -f - up -d --no-deps api <<YAML
services:
  api:
    image: $IMAGE
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
    environment:
      APP_REVISION: $REVISION
YAML
tar -xzf "$RELEASE_DIR/previous-application.tar.gz" -C /opt/jucheng frontend/dist
docker compose exec -T nginx nginx -s reload
printf 'Restored previous application %s; business database and files retained.\n' "$REVISION"
