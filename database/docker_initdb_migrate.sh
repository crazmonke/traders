#!/bin/bash
# MySQL 컨테이너 최초 기동 시 init.sql(v1) 직후에 실행된다.
# 이게 없으면 새 볼륨으로 띄운 개발 DB만 v1 스키마로 남는다.
# 운영/기존 DB 는 database/migrate.sh 로 적용한다.
set -euo pipefail

DB="${MYSQL_DATABASE:-ai_trading}"
MIGRATIONS="/docker-entrypoint-initdb.d/migrations"

[ -d "$MIGRATIONS" ] || exit 0

run() { mysql --protocol=socket -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 "$DB" "$@"; }

run <<'EOSQL'
CREATE TABLE IF NOT EXISTS `schema_migrations` (
    `filename` VARCHAR(191) NOT NULL PRIMARY KEY,
    `applied_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
EOSQL

for path in "$MIGRATIONS"/*.sql; do
  [ -e "$path" ] || continue
  name="$(basename "$path")"
  echo "[initdb] 마이그레이션 적용: $name"
  run < "$path"
  run -e "INSERT INTO schema_migrations (filename) VALUES ('$name');"
done
