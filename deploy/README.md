# 자동 배포 (main 푸시 → upsignal.mycafe24.com)

`main` 브랜치에 푸시하면 GitHub Actions가 **테스트 → rsync 동기화 → 의존성 설치 → 서비스 재시작 → 헬스체크** 순서로 운영 서버에 배포합니다.

## 실제 운영 환경 (2026-08-31 확인)

| 항목 | 값 |
| --- | --- |
| 호스트 | `upsignal.mycafe24.com` (104.105.137.245), SSH 22 |
| OS | Ubuntu 24.04.4 LTS |
| 배포 계정 | `root` (키 인증, 비밀번호 아님) |
| 배포 경로 | `/var/www/traders` |
| 웹서버 | Apache 2.4.58, DocumentRoot `/var/www/traders/api/public` |
| HTTPS | Let's Encrypt (`upsignal.mycafe24.com`) |
| 런타임 | PHP 8.3.6 / Composer 2.10.3 / Python 3.12.3 |
| DB | MariaDB 호스트 네이티브 (`127.0.0.1:3306`, 외부 미개방) |
| Redis | **Docker 컨테이너** `traders-redis-1` (`redis:7-alpine`, 6379 퍼블리시) |
| Python 엔진 | Docker 컨테이너 `traders-python-engine-1` |
| 방화벽 | ufw active (OpenSSH, 80, 443 허용) + fail2ban(sshd jail) |

## 파이프라인

```
push to main
  └─ ci  (.github/workflows/ci.yml 재사용: pytest + composer install)
       └─ deploy
            1. SSH 키 구성 (secrets.DEPLOY_SSH_KEY)
            2. rsync -az --delete  ./ → /var/www/traders
               (.env / api/vendor / trading_engine/venv / .git 은 서버 것 유지)
            3. deploy/scripts/remote_deploy.sh
               - composer install --no-dev -o
               - venv 생성 + pip install -r requirements.txt
               - systemctl restart ai-trading-engine / ai-trading-scheduler (등록된 경우만)
               - systemctl reload php*-fpm / apache2
            4. https://upsignal.mycafe24.com/api/health 헬스체크
               (HTTP 200 + status=ok, DB·Redis 연결까지 검사, 최대 10회 재시도)
```

> **스키마 변경이 포함된 배포**는 위 흐름에 마이그레이션이 들어 있지 않습니다.
> 푸시 후 서버에서 `./database/migrate.sh`를 직접 실행하세요. (`database/README.md`)

테스트가 실패하면 배포되지 않습니다. 긴급 배포는 Actions 탭 → **Deploy** → *Run workflow* → `skip_tests` 체크.

## 설정 상태

이미 완료된 항목:

- [x] 배포용 키페어 생성 — `~/.ssh/upsignal_deploy` / `.pub`
- [x] 서버 `/root/.ssh/authorized_keys` 에 공개키 등록 (`ssh-copy-id`)
- [x] GitHub Secret `DEPLOY_SSH_KEY` (개인키)
- [x] GitHub Secret `DEPLOY_KNOWN_HOSTS` (호스트 키 고정)
- [x] 워크플로 기본값을 실제 환경(`root`, `/var/www/traders`)에 맞춤

기본값과 다르게 쓰고 싶을 때만 `Settings → Secrets and variables → Actions → Variables` 에 추가:

| 이름 | 기본값 |
| --- | --- |
| `DEPLOY_HOST` | `upsignal.mycafe24.com` |
| `DEPLOY_USER` | `root` |
| `DEPLOY_PORT` | `22` |
| `DEPLOY_PATH` | `/var/www/traders` |
| `DEPLOY_HEALTH_URL` | `https://upsignal.mycafe24.com/api/health` |

## 남은 작업 (배포 파이프라인과 별개, 앱 동작에 필요)

서버 `/var/www/traders/.env` 가 아직 로컬/Docker 템플릿 상태입니다. Docker가 아니라 네이티브 구성이므로 아래 값을 고쳐야 앱이 DB에 붙습니다.

| 키 | 현재 | 필요한 값 |
| --- | --- | --- |
| `APP_ENV` | `local` | `production` |
| `DB_HOST` | `mysql` | `127.0.0.1` |
| `REDIS_HOST` | `redis` | `127.0.0.1` |
| `APP_URL` | `http://localhost:8080` | `https://upsignal.mycafe24.com` |
| `JWT_SECRET` | `change_me_...` | `openssl rand -hex 32` 결과 |

위 값은 **호스트(Apache/PHP) 기준**입니다. 컨테이너에서 도는 Python 엔진은 같은 값으로 붙을 수 없으므로
`docker-compose.override.yml` 에서 컨테이너 기준 값으로 덮어써야 합니다.

```yaml
services:
  python-engine:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      DB_HOST: host.docker.internal
      REDIS_HOST: redis          # 없으면 컨테이너가 .env의 127.0.0.1을 읽어 실패
```

### Redis — 호스트에 설치하지 마세요

Redis는 기획상 필수(캐싱 + `channel:signals` Pub/Sub)이며, **이미 Docker 컨테이너로 동작 중**입니다.
`apt install redis-server` 로 호스트에 또 설치하면 컨테이너가 점유한 6379 포트와 충돌해 기동에 실패합니다.
(php-redis 확장 설치 시 의존 패키지로 딸려 들어오는 경우가 있습니다.)

```bash
systemctl disable --now redis-server   # 호스트 여분 설치본 정리
redis-cli ping                          # PONG → 컨테이너 Redis 정상
```

Python 엔진/스케줄러를 상시 구동하려면 systemd 유닛 등록:

```bash
cp /var/www/traders/deploy/systemd/ai-trading-*.service /etc/systemd/system/
sed -i 's/REPLACE_WITH_DEPLOY_USER/root/' /etc/systemd/system/ai-trading-*.service
sed -i 's#/var/www/ai-trading#/var/www/traders#g' /etc/systemd/system/ai-trading-*.service
systemctl daemon-reload
systemctl enable --now ai-trading-engine ai-trading-scheduler
```

> 등록 전까지는 배포 스크립트가 "미등록 - 건너뜁니다" 경고만 남기고 정상 진행합니다.

**두 유닛의 역할이 다릅니다.**

| 유닛 | 하는 일 |
| --- | --- |
| `ai-trading-engine` | 거래소 수집 · 지표 · 신호 생성 (상시) |
| `ai-trading-scheduler` | **시그널 성과 추적 (Step 7)** — 과거 신호가 실제로 어떻게 됐는지 `ai_signal_results` 에 기록 |

추적기를 **별도 프로세스로 나눈 이유**는 거래소에서 과거 캔들을 받아오는 동안 수집이
멈추면 안 되기 때문입니다. 두 프로세스가 겹쳐 돌아도 Redis 분산 락(`lock:signal-result-tracker`)
이 중복 실행을 막고, 기록 자체도 `INSERT ... ON DUPLICATE KEY UPDATE` 라 안전합니다.

동작 확인:

```bash
systemctl status ai-trading-scheduler
journalctl -u ai-trading-scheduler -n 50 --no-pager   # "평가 완료 N건 기록"
```

### 적중률 조회 — **반드시 배점표 버전으로 나눈다**

배점표를 고치면 그 전후의 신호는 다른 규칙으로 만들어진 다른 것이다. 섞어서 평균을
내면 "고쳤더니 나아졌는가"를 알 수 없고, 대외적으로 말하는 적중률도 무엇의 적중률인지
불분명해진다(`docs/LEGAL.md`). `ai_signals.scoring_version` 이 그 기준이다.

```bash
mysql ai_trading -e "
SELECT s.scoring_version 배점표, r.horizon 제한, COUNT(*) 건수,
       ROUND(AVG(r.is_accurate)*100,1) 적중률,
       SUM(r.exit_reason='TAKE_PROFIT') 익절,
       SUM(r.exit_reason='STOP_LOSS')   손절,
       SUM(r.exit_reason='TIME_LIMIT')  시간초과
FROM ai_signal_results r
JOIN ai_signals s ON s.id = r.signal_id
GROUP BY s.scoring_version, r.horizon
ORDER BY s.scoring_version, FIELD(r.horizon,'5m','15m','1h','4h','1d');"
```

`GROUP BY s.scoring_version` 을 빼면 안 된다. 버전은 신호를 만들 때 엔진이 새기므로
(`trading_engine/strategy/versioning.py`) 날짜를 찾아 넣을 필요가 없다.

## rsync 동기화 규칙

`--delete`로 서버를 저장소 상태에 맞춰 미러링하되, 아래는 제외되어 **서버 파일이 보존**됩니다.

```
.git  .github  .env  .env.local  .env.bak.*  docker-compose.override.yml
api/vendor  trading_engine/venv  __pycache__  *.pyc  .pytest_cache
logs  *.log  mysql-data  redis-data  .DS_Store
```

서버에만 두는 파일은 이 목록에 추가해야 배포 때 삭제되지 않습니다.

## fail2ban 주의

sshd jail이 켜져 있어 **비밀번호/키 인증에 여러 번 실패하면 해당 IP가 약 10분간 차단**됩니다(웹은 정상, 22번 포트만 거부). 사무실 고정 IP를 예외로 등록하려면 서버에서:

```bash
printf '[DEFAULT]\nignoreip = 127.0.0.1/8 ::1 14.47.47.47\n' > /etc/fail2ban/jail.d/ignoreip.local
fail2ban-client set sshd unbanip 14.47.47.47
fail2ban-client reload
```

GitHub Actions 러너는 등록된 키로 첫 시도에 성공하므로 차단 대상이 되지 않습니다.

## 문제 해결

| 증상 | 원인/조치 |
| --- | --- |
| `Permission denied (publickey)` | 서버 `/root/.ssh/authorized_keys` 확인, `DEPLOY_USER` 변수 확인 |
| `Connection refused` (22번) | fail2ban 차단 — 위 섹션 참고, 약 10분 후 자동 해제 |
| `test -d '/var/www/traders'` 실패 | 경로 확인 또는 `DEPLOY_PATH` 변수 수정 |
| `.env 가 없습니다` | 서버 `/var/www/traders/.env` 생성 |
| 헬스체크 실패 | `curl https://upsignal.mycafe24.com/api/health` 로 어느 검사가 깨졌는지 확인 후<br>`tail -50 /var/log/apache2/ai-trading-error.log` |
