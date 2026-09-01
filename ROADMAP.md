# 구현 로드맵 (prompt.md Step 1~7)

2026-09-01 착수. **하루에 한 스텝**씩 진행한다.
각 스텝을 끝내면 해당 줄의 `[ ]`를 `[x]`로 바꾸고, "완료일"과 실제 산출물 경로를 채운다.

> 판단 기준: 구현 상태는 **코드**로만 판정한다. `README.md`는 사업 기획 문서이므로
> 여기 적힌 내용은 "구현됨"이 아니라 "하기로 한 것"이다.

## 착수 전 실측 (2026-09-01)

전체 진행률 ≈ **10~15%**. 인프라와 DB 설계는 끝났고 애플리케이션 로직은 0줄.

완료된 것:

- `database/init.sql` — prompt.md §2의 6개 테이블 전부
- `docker-compose.yml` — mysql / redis / php-api / python-engine + healthcheck
- `deploy/`, `.github/workflows/` — rsync 배포, systemd 유닛, nginx conf, health 기반 배포 판정
- `.env.example` — Upbit / OpenAI / JWT / RateLimit 키 규약

## 스텝 목록

| 완료 | 스텝 | 목표 | 착수 전 상태 | 완료일 |
|---|---|---|---|---|
| [ ] | **1** | Upbit WebSocket 수집 + 지표 엔진 (`market/`, `indicators/`) | 0% — `main.py` 8줄 placeholder, 빈 패키지 | |
| [ ] | **2** | RuleEngine + OpenAI 연동 + `ai_signals` 저장 (`strategy/`, `ai/`, `database/`) | 0% — 빈 패키지 | |
| [ ] | **3** | 백테스팅 엔진, 수수료·슬리피지 반영 (`backtest/`) | 0% — 빈 패키지 | |
| [ ] | **4** | 실거래 안전장치 + 매매 실행 (`trading/`) | 0% — 테이블만 있고 읽는 코드 없음 | |
| [ ] | **5** | PHP REST API (signals / market / backtest / safety, JWT, Rate Limit) | ~5% — 라우터·CORS 골격 + `/api/health`만 | |
| [ ] | **6** | 시그널 성과 추적 스케줄러 데몬 | 0% — systemd 유닛만 있고 실행할 코드 없음 | |
| [ ] | **7** | 테스트 · 구조화 로깅 · 알림 훅 | ~25% — Compose만 완료, 테스트 0건 | |
| [ ] | **8** | 대시보드 UI (신호 목록·상세·차트) | 0% — `api/public/index.html`은 정적 안내 페이지 | |

## 스텝별 완료 정의 (DoD)

각 스텝의 상세 요구사항은 `prompt.md`의 해당 `[Step N]` 프롬프트를 원본으로 삼는다.
아래는 "끝났다"고 말하기 위한 최소 조건이다.

- **Step 1** — 5개 마켓(KRW-BTC/ETH/XRP/SOL/DOGE) ticker·orderbook이 Redis에 실시간 적재되고,
  RSI(14) / MACD(12,26,9) / MA(5,20,60) 정배열 / 호가 불균형이 계산된다. 끊기면 지수 백오프로 재접속한다.
- **Step 2** — `S_Tech`가 prompt.md §3.2 배점표대로 산출되고 `min(sum, 100)`으로 clamp된다.
  70 이상 / 30 이하일 때만 OpenAI를 호출하고, Redis TTL 키로 중복 호출을 막는다.
  확률 합계가 95~105를 벗어나면 HOLD로 강등한다. 결과가 `ai_signals`에 저장되고 `channel:signals`로 publish된다.
- **Step 3** — 수수료 0.05%(매수·매도 각각), 슬리피지 0.05%가 반영된다.
  총수익률 / 승률 / 거래수 / 손익비 / MDD를 내고 `backtest_logs`에 저장한다. 재현성 테스트 1개 이상.
- **Step 4** — 기본 모드가 **PAPER**다. LIVE 전환은 명시적 조작 + 감사 로그를 남긴다.
  일일 손실 한도 초과 시 `kill_switch_active=1`로 신규 주문을 자동 차단하고, 수동 해제 전까지 유지한다.
  단일 주문이 `max_position_size_krw`를 넘지 않는다. 기동 시 Upbit 키에 출금 권한이 없는지 검증한다.
- **Step 5** — `GET /signals/latest`(페이지네이션), `GET /signals/strong`, `GET /market/summary`,
  `POST /backtest/run`, `GET|PATCH /safety/state`가 동작한다. 공통 에러 포맷, JWT 인증,
  분당 60회 Rate Limit, 전 쿼리 Prepared Statement.
- **Step 6** — 5m/15m/1h horizon 미평가 건만 골라 `INSERT ... ON DUPLICATE KEY UPDATE`로 기록한다.
  BUY 계열 +0.2% 초과 / SELL 계열 -0.2% 미만이면 `is_accurate=1`, HOLD는 제외.
  Redis 분산 락으로 중복 실행에도 안전하다.
- **Step 7** — RuleEngine clamp(100 상한), 백테스트, 성과 추적에 pytest 단위 테스트가 있다.
  `ci.yml`의 "exit 5 우회"를 제거한다. Python·PHP 양쪽 JSON 구조화 로깅과 에러 알림 훅을 넣는다.
- **Step 8** — 브라우저에서 신호 목록·상세를 보고, 마켓별 점수 추이 차트를 확인할 수 있다.
  Step 5의 API만 호출하며 DB에 직접 붙지 않는다. 모든 화면 하단에 prompt.md §6-4의 법적 고지를 노출한다.
  (prompt.md의 Step 1~7 범위 밖. 9/1 논의에서 추가 합의.)

## 확정 일정 (주말 포함, Step 1·5는 2일)

| 날짜 | 진행 |
|---|---|
| 9/1 (화) | Step 1-a — Upbit WebSocket 수집 + Redis 캐싱 |
| 9/2 (수) | Step 1-b — 지표 계산(RSI/MACD/MA/호가불균형) + 재접속 백오프 |
| 9/3 (목) | Step 2 — RuleEngine + OpenAI + ai_signals 저장 |
| 9/4 (금) | Step 3 — 백테스팅 엔진 |
| 9/5 (토) | Step 4 — 실거래 안전장치·매매 실행 |
| 9/6 (일) | Step 5-a — 엔드포인트 5종 + 공통 에러 포맷 |
| 9/7 (월) | Step 5-b — JWT 인증 + Rate Limit |
| 9/8 (화) | Step 6 — 성과 추적 스케줄러 |
| 9/9 (수) | Step 7 — 테스트 · 로깅 · 알림 훅 |
| 9/10 (목) | Step 8-a — 신호 목록·상세 화면 |
| 9/11 (금) | Step 8-b — 점수 추이 차트 + 법적 고지 |
