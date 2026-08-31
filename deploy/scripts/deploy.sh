#!/usr/bin/env bash
# 서버 배포 스크립트 템플릿 (서버가 정해지면 SSH 접속 정보/경로를 채워서 사용)
# 사용법: ./deploy/scripts/deploy.sh
set -euo pipefail

REMOTE_HOST="REPLACE_WITH_SERVER_HOST"
REMOTE_USER="REPLACE_WITH_DEPLOY_USER"
REMOTE_PATH="/var/www/ai-trading"

echo "==> 코드 동기화"
rsync -az --exclude-from='.gitignore' --exclude '.git' ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"

echo "==> 원격 서버에서 의존성 설치 및 서비스 재시작"
ssh "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<'EOF'
set -euo pipefail
cd /var/www/ai-trading
composer install --no-interaction --no-dev --optimize-autoloader -d api
source trading_engine/venv/bin/activate
pip install -r trading_engine/requirements.txt
deactivate
sudo systemctl restart ai-trading-engine
sudo systemctl restart ai-trading-scheduler
sudo systemctl reload php-fpm
EOF

echo "==> 배포 완료"
