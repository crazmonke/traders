# DB 스키마 관리

| 파일 | 역할 |
| --- | --- |
| `init.sql` | v1 스키마. **수정하지 않는다.** 컨테이너 최초 기동 시에만 실행된다 |
| `migrations/NNN_*.sql` | 스키마 변경분. 순번 순서대로 한 번씩만 적용된다 |
| `migrate.sh` | 기존 DB에 미적용 마이그레이션을 적용하는 러너 |
| `docker_initdb_migrate.sh` | 컨테이너 최초 기동 시 `init.sql` 직후 자동 실행 (신규 설치 전용) |

적용 이력은 DB 안의 `schema_migrations` 테이블에 파일명으로 기록된다.

> **수동으로 SQL 을 실행했다면 원장에도 넣어야 한다.** 안 넣으면 다음 `migrate.sh` 가
> 그 파일을 다시 적용하려다 `Duplicate column` 으로 멈추고, 그 뒤 순번이 전부 밀린다.
> `INSERT IGNORE INTO schema_migrations (filename) VALUES ('003_....sql');`

## 스키마를 바꿀 때

`init.sql`을 고치지 말고 `migrations/`에 다음 순번 파일을 추가한다.
`init.sql`은 이미 데이터가 있는 DB에 다시 실행되지 않으므로, 거기에만 쓴 변경은 운영에 반영되지 않는다.

## 적용 방법

```bash
# 신규 설치 (docker compose) — 자동 적용. 별도 조작 불필요
docker compose up -d mysql

# 기존 DB — .env 의 접속 정보를 사용
./database/migrate.sh

# 접속 정보를 일회성으로 지정 (환경변수가 .env 보다 우선한다)
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USERNAME=root DB_PASSWORD=... ./database/migrate.sh

# 적용 현황만 확인
./database/migrate.sh --status
```

## 주의

- **배포 파이프라인은 마이그레이션을 자동 적용하지 않는다.** 스키마 변경이 포함된 배포는
  푸시 후 서버에서 `./database/migrate.sh`를 직접 실행해야 한다.
- DDL은 트랜잭션으로 롤백되지 않는다. 중간에 실패하면 그 파일은 "미적용"으로 남지만
  이미 실행된 문장은 되돌아가지 않는다. 원인을 고치고 다시 실행하기 전에
  어디까지 반영됐는지 확인할 것.
