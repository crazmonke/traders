#!/usr/bin/env bash
# 로컬 개발 서버. http://127.0.0.1:8080/app/
#
# 저장소 .env 의 DB_HOST/REDIS_HOST 는 **도커 서비스명**(mysql/redis)이라 호스트에서
# PHP 를 직접 띄우면 해석되지 않는다. 그 두 개만 127.0.0.1 로 바꿔 실행한다.
# .env 를 고치지 않는 이유는 그 값이 docker-compose 배포에서는 맞기 때문이다.
set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${PORT:-8080}"
OVERRIDE="$(mktemp -t traders-local-XXXX.php)"
trap 'rm -f "$OVERRIDE"' EXIT

cat > "$OVERRIDE" <<'PHP'
<?php
// 로컬 실행용. 컨테이너 포트가 호스트에 열려 있다는 전제.
$_ENV['DB_HOST'] = getenv('LOCAL_DB_HOST') ?: '127.0.0.1';
$_ENV['REDIS_HOST'] = getenv('LOCAL_REDIS_HOST') ?: '127.0.0.1';
PHP

echo "Traders 로컬 서버 → http://127.0.0.1:${PORT}/app/"
echo "(Ctrl+C 로 종료)"
exec php -d auto_prepend_file="$OVERRIDE" -S "127.0.0.1:${PORT}" \
    -t api/public api/public/index.php
