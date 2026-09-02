#!/usr/bin/env bash
# database/migrations/*.sql 중 아직 적용되지 않은 것만 순번대로 적용한다.
#
#   ./database/migrate.sh            # 적용
#   ./database/migrate.sh --status   # 적용 현황만 출력
#
# 접속 정보는 .env(없으면 환경변수, 그것도 없으면 .env.example 기본값)에서 읽는다.
# DDL 은 트랜잭션으로 롤백되지 않으므로, 실패하면 그 파일은 미적용으로 남는다.
# 원인을 고치고 다시 실행하면 된다 — 단, 이미 실행된 문장은 되돌아가지 않는다.
set -euo pipefail

cd "$(dirname "$0")/.."

# .env 는 "기본값"이다. 이미 환경에 있는 값은 덮어쓰지 않는다
# (DB_HOST=127.0.0.1 ./database/migrate.sh 처럼 일회성 지정이 먹혀야 한다).
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    if [ -z "${!key:-}" ]; then
      export "$key=$value"
    fi
  done < .env
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_DATABASE="${DB_DATABASE:-ai_trading}"
DB_USERNAME="${DB_USERNAME:-ai_trading}"
DB_PASSWORD="${DB_PASSWORD:-}"

MIGRATION_DIR="database/migrations"

mysql_run() {
  MYSQL_PWD="$DB_PASSWORD" mysql \
    --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USERNAME" \
    --default-character-set=utf8mb4 --batch --skip-column-names "$DB_DATABASE" "$@"
}

mysql_run <<'EOSQL'
CREATE TABLE IF NOT EXISTS `schema_migrations` (
    `filename` VARCHAR(191) NOT NULL PRIMARY KEY,
    `applied_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
EOSQL

applied="$(mysql_run -e 'SELECT filename FROM schema_migrations;')"

is_applied() {
  printf '%s\n' "$applied" | grep -Fxq "$1"
}

if [ "${1:-}" = "--status" ]; then
  for path in "$MIGRATION_DIR"/*.sql; do
    name="$(basename "$path")"
    if is_applied "$name"; then echo "적용됨   $name"; else echo "미적용   $name"; fi
  done
  exit 0
fi

pending=0
for path in "$MIGRATION_DIR"/*.sql; do
  name="$(basename "$path")"
  if is_applied "$name"; then
    continue
  fi
  echo "적용 중: $name"
  mysql_run < "$path"
  mysql_run -e "INSERT INTO schema_migrations (filename) VALUES ('$name');"
  echo "적용 완료: $name"
  pending=$((pending + 1))
done

if [ "$pending" -eq 0 ]; then
  echo "적용할 마이그레이션 없음 (스키마 최신)"
else
  echo "마이그레이션 ${pending}건 적용 완료"
fi
