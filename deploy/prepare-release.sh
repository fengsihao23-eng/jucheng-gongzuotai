#!/usr/bin/env bash
# Runs on the existing production host, before source synchronization.
set -euo pipefail
RELEASE_DIR="${1:?release directory}"
REVISION="${2:?revision}"
[[ "$RELEASE_DIR" =~ ^/opt/jucheng/deploy/releases/[0-9]{8}-[0-9]{6}-[a-f0-9]{12}$ ]] || exit 2
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || exit 2
cd /opt/jucheng/deploy
test -f ../backend/.env
test -f .env
umask 077
mkdir -p "$RELEASE_DIR"
docker compose exec -T db pg_dump -U jucheng -Fc jucheng > "$RELEASE_DIR/database.dump"
test -s "$RELEASE_DIR/database.dump"
docker compose exec -T db pg_restore --list < "$RELEASE_DIR/database.dump" > "$RELEASE_DIR/database.contents"
docker compose exec -T api tar -czf - -C /data files > "$RELEASE_DIR/files.tar.gz"
tar -tzf "$RELEASE_DIR/files.tar.gz" > "$RELEASE_DIR/files.contents"
tar -czf "$RELEASE_DIR/previous-application.tar.gz" -C /opt/jucheng frontend/dist deploy/docker-compose.yml deploy/nginx.conf deploy/.env backend/.env
API_IMAGE=$(docker inspect --format '{{.Image}}' deploy-api-1)
docker compose exec -T api python -c 'from app.core.config import settings; print(settings.app_revision)' > "$RELEASE_DIR/previous-revision.txt"
docker tag "$API_IMAGE" "jucheng-api-rollback:${REVISION:0:12}"
printf '%s\n' "$API_IMAGE" > "$RELEASE_DIR/previous-image.txt"
printf '%s\n' "$REVISION" > "$RELEASE_DIR/target-revision.txt"
printf 'Release backup verified: %s\n' "$RELEASE_DIR"
