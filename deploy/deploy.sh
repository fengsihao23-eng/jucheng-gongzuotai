#!/usr/bin/env bash
# 一键部署：本地构建产物 → 服务器 /opt/jucheng → docker compose 起栈
# 用法：
#   SERVER=root@1.2.3.4 ./deploy/deploy.sh            # 首次/更新部署
#   SERVER=root@1.2.3.4 ./deploy/deploy.sh --seed     # 首次部署并写入演示数据（会清空业务表）
set -euo pipefail

# Existing installations use the verified release path. Keep the explicit first-install
# --seed workflow separate so updates cannot reset business records.
if [[ "${1:-}" != "--seed" ]]; then
  exec bash "$(cd "$(dirname "$0")" && pwd)/release.sh" "$@"
fi

SERVER="${SERVER:?用法: SERVER=root@<ECS公网IP> $0 [--seed]}"
REMOTE_DIR="${REMOTE_DIR:-/opt/jucheng}"
SEED="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SSH=(ssh "${SSH_OPTS[@]}" "$SERVER")
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

say() { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m!!  %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$ROOT/deploy/.env" ]] || die "缺少 deploy/.env（DB_PASSWORD）"
[[ -f "$ROOT/deploy/app.env" ]] || die "缺少 deploy/app.env（生产环境变量）"
[[ -f "$ROOT/deploy/certs/fullchain.pem" ]] || die "缺少 deploy/certs/fullchain.pem"
# 2026-11-13 事故：本地 dist 停在 11-10，源码已到 11-12，部署把旧界面推上线，
# 老板看到「工作台回退」。产物比源码旧就自动重建，构建失败直接中止。
if [[ ! -f "$ROOT/frontend/dist/index.html" ]] \
   || [[ -n "$(find "$ROOT/frontend/src" -newer "$ROOT/frontend/dist/index.html" -print -quit 2>/dev/null)" ]]; then
  say "前端产物缺失或落后于源码，自动重新构建"
  if [[ -d "$ROOT/frontend/dist" ]]; then
    mv "$ROOT/frontend/dist" "$ROOT/frontend/dist_bak_$(date +%Y%m%d_%H%M%S)"
  fi
  ( cd "$ROOT/frontend" && npm run build ) || die "前端构建失败，已中止部署（避免把旧界面推上线）"
fi
[[ -f "$ROOT/frontend/dist/index.html" ]] || die "缺少 frontend/dist，请先 cd frontend && npm run build"

say "1/6 检查服务器连通性与 Docker"
"${SSH[@]}" 'set -e; docker --version >/dev/null 2>&1 || { echo NO_DOCKER; exit 3; }; docker compose version >/dev/null 2>&1 || { echo NO_COMPOSE; exit 4; }; docker info >/dev/null 2>&1 || { echo NO_DOCKER_PERM; exit 5; }' \
  || die "服务器不可达或未安装 Docker/Compose（可用 apt install docker.io docker-compose-v2 或 sudo curl -fsSL https://get.docker.com | sh）"

# 2026-11-12 事故：构建缓存堆到 12.6GB，镜像 68 个，本次构建把根分区写满，
# Postgres 无法写 WAL 进入崩溃循环。部署前先卡住剩余空间。
FREE_GB=$("${SSH[@]}" "df -Pk / | awk 'NR==2{print int(\$4/1048576)}'")
[[ "$FREE_GB" =~ ^[0-9]+$ ]] || die "无法读取服务器磁盘余量"
[[ "$FREE_GB" -ge 5 ]] || die "服务器根分区只剩 ${FREE_GB}GB，先清理再部署（否则数据库会因写满磁盘崩溃）"
say "磁盘余量 ${FREE_GB}GB"

say "2/6 创建目录 $REMOTE_DIR"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR/backend' '$REMOTE_DIR/frontend' '$REMOTE_DIR/deploy/backups'"

say "3/6 同步代码与构建产物"
RSYNC_EXCLUDES=(--exclude '.DS_Store' --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache'
  --exclude '.venv' --exclude 'var' --exclude 'node_modules' --exclude '.env')
rsync -az --delete -e "$RSYNC_SSH" "${RSYNC_EXCLUDES[@]}" "$ROOT/backend/" "$SERVER:$REMOTE_DIR/backend/"
rsync -az --delete -e "$RSYNC_SSH" --exclude '.DS_Store' "$ROOT/frontend/dist/" "$SERVER:$REMOTE_DIR/frontend/dist/"
rsync -az --delete -e "$RSYNC_SSH" --exclude '.DS_Store' --exclude 'backups' \
  "$ROOT/deploy/" "$SERVER:$REMOTE_DIR/deploy/"

say "4/6 安装生产环境变量（backend/.env, 600）"
REVISION="$(git -C "$ROOT" rev-parse HEAD)"
# .env 不入 Git；仅更新公开的版本号，保留数据库口令等配置。
if [[ -f "$ROOT/deploy/.env" ]]; then
  TASK_REVISION="$REVISION" python3 - "$ROOT/deploy/.env" <<'PY'
import os, sys
from pathlib import Path
p = Path(sys.argv[1])
lines = [line for line in p.read_text().splitlines() if not line.startswith('APP_REVISION=')]
p.write_text('\n'.join(lines) + '\nAPP_REVISION=' + os.environ['TASK_REVISION'] + '\n')
p.chmod(0o600)
PY
  scp -q "$ROOT/deploy/.env" "$SERVER:$REMOTE_DIR/deploy/.env"
fi
scp -q "$ROOT/deploy/app.env" "$SERVER:$REMOTE_DIR/backend/.env"
"${SSH[@]}" "chmod 600 '$REMOTE_DIR/backend/.env' '$REMOTE_DIR/deploy/.env' '$REMOTE_DIR/deploy/certs/privkey.pem'"

say "5/6 构建并启动容器（首次约 3-8 分钟）"
# 先回收旧构建缓存与悬空镜像：每次部署都会产生新层，累积起来足以写满根分区
"${SSH[@]}" "docker builder prune -f --filter until=168h >/dev/null 2>&1 || true; docker image prune -f >/dev/null 2>&1 || true"
"${SSH[@]}" "cd '$REMOTE_DIR/deploy' && docker compose --profile build build xiaoju-workspace && docker compose up -d --build --remove-orphans && docker compose exec -T nginx nginx -t && docker compose exec -T nginx nginx -s reload"

say "6/6 等待服务健康"
HEALTH_CMD='curl -fsS -m 5 -k -H "Host: juchengcc.com" https://127.0.0.1/health 2>/dev/null || wget -qO- --no-check-certificate --header="Host: juchengcc.com" https://127.0.0.1/health 2>/dev/null'
for i in $(seq 1 60); do
  if "${SSH[@]}" "$HEALTH_CMD" >/tmp/jc_health.json 2>/dev/null; then
    cat /tmp/jc_health.json; echo; break
  fi
  [[ $i == 60 ]] && { "${SSH[@]}" "cd '$REMOTE_DIR/deploy' && docker compose ps && docker compose logs --tail=60 api"; die "健康检查未通过"; }
  sleep 5
done

if [[ "$SEED" == "--seed" ]]; then
  say "写入演示数据（清空业务表）"
  "${SSH[@]}" "cd '$REMOTE_DIR/deploy' && docker compose exec -T api python -m scripts.seed"
fi

say "完成"
"${SSH[@]}" "cd '$REMOTE_DIR/deploy' && docker compose ps"
echo "站点：https://juchengcc.com   健康检查：https://juchengcc.com/health"
