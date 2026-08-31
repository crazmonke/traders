#!/usr/bin/env bash
# 수동 배포 스크립트 (자동 배포는 .github/workflows/deploy.yml 이 담당).
# 로컬 작업 트리를 그대로 서버에 밀어넣으므로, 평소에는 main 브랜치 푸시로 배포하세요.
#
# 사용법:
#   ./deploy/scripts/deploy.sh
#   DEPLOY_USER=ubuntu DEPLOY_PATH=/srv/ai-trading ./deploy/scripts/deploy.sh
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-upsignal.mycafe24.com}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_PATH="${DEPLOY_PATH:-/var/www/traders}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

SSH_OPTS="-p ${DEPLOY_PORT}"

echo "==> 대상: ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}"

echo "==> 코드 동기화 (rsync)"
rsync -az --delete --human-readable \
  --exclude '.git' \
  --exclude '.github' \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.bak.*' \
  --exclude 'docker-compose.override.yml' \
  --exclude 'api/vendor' \
  --exclude 'trading_engine/venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude 'logs' \
  --exclude '*.log' \
  --exclude 'mysql-data' \
  --exclude 'redis-data' \
  --exclude '.DS_Store' \
  -e "ssh ${SSH_OPTS}" \
  ./ "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/"

echo "==> 원격 배포 스크립트 실행"
# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${DEPLOY_USER}@${DEPLOY_HOST}" \
  "DEPLOY_PATH='${DEPLOY_PATH}' bash '${DEPLOY_PATH}/deploy/scripts/remote_deploy.sh'"

echo "==> 배포 완료"
