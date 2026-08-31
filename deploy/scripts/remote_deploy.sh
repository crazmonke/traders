#!/usr/bin/env bash
# 서버에서 실행되는 배포 후처리 스크립트.
# GitHub Actions(.github/workflows/deploy.yml)가 rsync 후 SSH로 호출하며,
# 수동 배포(deploy/scripts/deploy.sh)에서도 동일하게 사용한다.
#
# 환경변수로 덮어쓸 수 있는 값:
#   DEPLOY_PATH        배포 경로 (기본 /var/www/traders)
#   ENGINE_SERVICE     매매 엔진 systemd 유닛명 (기본 ai-trading-engine)
#   SCHEDULER_SERVICE  스케줄러 systemd 유닛명 (기본 ai-trading-scheduler)
#   WEB_SERVICE        웹서버 유닛명 (기본: 자동 감지 apache2 → nginx)
set -euo pipefail

APP_DIR="${DEPLOY_PATH:-/var/www/traders}"
ENGINE_SERVICE="${ENGINE_SERVICE:-ai-trading-engine}"
SCHEDULER_SERVICE="${SCHEDULER_SERVICE:-ai-trading-scheduler}"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf '[경고] %s\n' "$*" >&2; }

[ -d "$APP_DIR" ] || { echo "[오류] 배포 경로가 없습니다: $APP_DIR" >&2; exit 1; }
cd "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "[오류] $APP_DIR/.env 가 없습니다. .env.example을 참고해 서버에 직접 생성하세요(.env는 배포로 덮어쓰지 않습니다)." >&2
  exit 1
fi

# ---------------------------------------------------------------- sudo 준비
SUDO=""
SKIP_SERVICES=0
if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true 2>/dev/null; then
    SUDO="sudo -n"
  else
    SKIP_SERVICES=1
    warn "비밀번호 없는 sudo를 쓸 수 없어 서비스 재시작을 건너뜁니다. deploy/README.md의 sudoers 설정을 확인하세요."
  fi
fi

# ------------------------------------------------------------ PHP 의존성
if command -v composer >/dev/null 2>&1; then
  log "PHP 의존성 설치 (composer)"
  composer install --working-dir="$APP_DIR/api" \
    --no-interaction --no-dev --optimize-autoloader --no-progress
else
  warn "composer 를 찾을 수 없어 PHP 의존성 설치를 건너뜁니다 (기존 api/vendor 유지)."
fi

# --------------------------------------------------------- Python 의존성
PY_DIR="$APP_DIR/trading_engine"
if [ -f "$PY_DIR/requirements.txt" ]; then
  if [ ! -x "$PY_DIR/venv/bin/python" ]; then
    log "Python 가상환경 생성"
    python3 -m venv "$PY_DIR/venv"
  fi
  log "Python 의존성 설치"
  "$PY_DIR/venv/bin/pip" install --quiet --upgrade pip
  "$PY_DIR/venv/bin/pip" install --quiet -r "$PY_DIR/requirements.txt"
fi

# ------------------------------------------------------------ 서비스 재시작
unit_exists() {
  systemctl list-unit-files --no-legend "$1.service" 2>/dev/null | grep -q .
}

restart_unit() {
  local unit="$1" action="${2:-restart}"
  if [ "$SKIP_SERVICES" -eq 1 ]; then
    warn "$unit $action 건너뜀 (sudo 없음)"
    return 0
  fi
  if ! unit_exists "$unit"; then
    warn "$unit.service 가 등록되어 있지 않아 건너뜁니다."
    return 0
  fi
  log "$unit $action"
  $SUDO systemctl "$action" "$unit"
  $SUDO systemctl is-active --quiet "$unit" \
    && echo "$unit: active" \
    || { echo "[오류] $unit 이 정상 기동하지 않았습니다." >&2
         $SUDO journalctl -u "$unit" -n 30 --no-pager >&2 || true
         exit 1; }
}

restart_unit "$ENGINE_SERVICE"
restart_unit "$SCHEDULER_SERVICE"

# PHP-FPM (설치된 버전 자동 감지)
for fpm in $(systemctl list-unit-files --no-legend 'php*-fpm.service' 2>/dev/null | awk '{print $1}' | sed 's/\.service$//'); do
  restart_unit "$fpm" reload
done

# 웹서버
WEB_SERVICE="${WEB_SERVICE:-}"
if [ -z "$WEB_SERVICE" ]; then
  for candidate in apache2 nginx httpd; do
    if unit_exists "$candidate"; then WEB_SERVICE="$candidate"; break; fi
  done
fi
[ -n "$WEB_SERVICE" ] && restart_unit "$WEB_SERVICE" reload || warn "웹서버 유닛을 찾지 못했습니다."

log "배포 완료 ($APP_DIR)"
