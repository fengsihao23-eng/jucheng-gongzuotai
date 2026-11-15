#!/usr/bin/env bash
# Deploy a tested, committed checkout to the existing installation, with backup and version checks.
set -euo pipefail
SERVER="${SERVER:?Set SERVER to the configured production SSH host}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REVISION="$(git -C "$ROOT" rev-parse HEAD)"
RELEASE_ID="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)-${REVISION:0:12}"
RELEASE_DIR="/opt/jucheng/deploy/releases/$RELEASE_ID"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER")
test "${1:-}" != "--seed" || { echo 'Production releases never seed the database'; exit 2; }
test -z "$(git -C "$ROOT" diff --name-only HEAD -- backend/app backend/alembic backend/scripts frontend/src frontend/scripts frontend/vite.config.js frontend/package.json frontend/package-lock.json deploy)" || { echo 'Commit release sources before deployment'; exit 2; }
test -z "$(git -C "$ROOT" ls-files --others --exclude-standard -- backend/app backend/alembic backend/scripts frontend/src frontend/scripts deploy)" || { echo 'Release contains untracked source files'; exit 2; }
(cd "$ROOT/frontend" && npm run build)
node --input-type=module - "$ROOT" "$REVISION" <<'JS'
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, revision] = process.argv.slice(2);
const { buildInfo } = await import(pathToFileURL(root + '/frontend/scripts/build-info.mjs'));
const actual = JSON.parse(fs.readFileSync(root + '/frontend/dist/build-info.json', 'utf8'));
if (actual.revision !== revision || actual.source_sha256 !== buildInfo().source_sha256) throw new Error('Build does not match release sources');
JS
"${SSH[@]}" 'test -f /opt/jucheng/backend/.env && test -f /opt/jucheng/deploy/.env && docker info >/dev/null && test "$(df -Pk /opt/jucheng | awk '\''NR==2 {print int($4/1048576)}'\'')" -ge 8'
"${SSH[@]}" bash -s -- "$RELEASE_DIR" "$REVISION" < "$ROOT/deploy/prepare-release.sh"
# Exclude local test data and credentials; installed production configuration remains authoritative.
rsync -az -e 'ssh -o BatchMode=yes' --exclude '.venv' --exclude 'var' --exclude 'node_modules' --exclude '.env' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' --exclude 'samples' "$ROOT/backend/" "$SERVER:/opt/jucheng/backend/"
rsync -az -e 'ssh -o BatchMode=yes' --exclude '.env' --exclude 'app.env' --exclude 'certs' --exclude 'backups' --exclude 'releases' "$ROOT/deploy/" "$SERVER:/opt/jucheng/deploy/"
rsync -az -e 'ssh -o BatchMode=yes' --exclude 'index.html' --exclude 'build-info.json' "$ROOT/frontend/dist/" "$SERVER:/opt/jucheng/frontend/dist/"
scp -q "$ROOT/frontend/dist/index.html" "$ROOT/frontend/dist/build-info.json" "$SERVER:$RELEASE_DIR/"
# Keep prior hashed assets for already-open browsers; do not replace business volumes or secrets.
"${SSH[@]}" bash -s -- "$REVISION" <<'REMOTE'
set -euo pipefail
cd /opt/jucheng/deploy
python3 - "$1" <<'PY'
from pathlib import Path
import sys
p = Path('.env')
lines = [line for line in p.read_text().splitlines() if not line.startswith('APP_REVISION=')]
p.write_text('\n'.join(lines) + '\nAPP_REVISION=' + sys.argv[1] + '\n')
p.chmod(0o600)
PY
docker compose build api
docker compose up -d --no-deps api
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
REMOTE
for attempt in $(seq 1 40); do
  if "${SSH[@]}" "curl -fsS --max-time 8 https://juchengcc.com/health" | node -e 'let s="";process.stdin.on("data",c=>s+=c);process.stdin.on("end",()=>{try{const j=JSON.parse(s);process.exit(j.status==="ok" && j.revision===process.argv[1]?0:1)}catch{process.exit(1)}})' "$REVISION"; then
    # Publish the entry page only after the compatible new API is healthy.
    "${SSH[@]}" "cp '$RELEASE_DIR/index.html' /opt/jucheng/frontend/dist/index.html.next && cp '$RELEASE_DIR/build-info.json' /opt/jucheng/frontend/dist/build-info.json.next && mv /opt/jucheng/frontend/dist/build-info.json.next /opt/jucheng/frontend/dist/build-info.json && mv /opt/jucheng/frontend/dist/index.html.next /opt/jucheng/frontend/dist/index.html"
    "${SSH[@]}" "curl -fsS --max-time 8 https://juchengcc.com/build-info.json" | node -e 'let s="";process.stdin.on("data",c=>s+=c);process.stdin.on("end",()=>{try{process.exit(JSON.parse(s).revision===process.argv[1]?0:1)}catch{process.exit(1)}})' "$REVISION"
    printf 'Deployed %s; backup %s\n' "$REVISION" "$RELEASE_DIR"
    exit 0
  fi
  sleep 3
done
printf 'Version verification failed. Retained backup: %s\n' "$RELEASE_DIR"
exit 1
