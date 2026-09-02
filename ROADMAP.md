# 구현 로드맵 (prompt.md v2, Step 1~7)

2026-09-01 착수. 2026-09-02 **다중 거래소 전환 결정**으로 로드맵을 재구성한다. **하루에 한 스텝**씩 진행한다.
각 스텝을 끝내면 해당 줄의 `[ ]`를 `[x]`로 바꾸고, "완료일"과 실제 산출물 경로를 채운다.
> 판단 기준: 구현 상태는 **코드**로만 판정한다. `README.md`는 사업 기획 문서이므로
> 여기 적힌 내용은 "구현됨"이 아니라 "하기로 한 것"이다.

## 2026-09-02 방향 전환 요약

- 신호 데이터 소스: 업비트 단일 → **`ccxt` 기반 다중 거래소(Binance/OKX/Bybit/Coinbase/Upbit)**
- 지표 4종 추가: Bollinger Bands, Stochastic, ADX, CCI
- 신규 개념: 거래소 간 합의(Exchange Consensus)
- **트레이딩뷰는 공개 데이터 API가 없음이 확인됨** → 시세/지표 소스로 사용하지 않고, **유저별 웹훅 URL을 발급해 유저 본인 트레이딩뷰 계정의 알림을 개인 대시보드로 중계하는 멀티테넌트 PRO 기능("Bring Your Own TradingView")**으로 스코프 확정
- 자동매매 실행은 기존과 동일하게 **업비트 전용, 본인 계정만**
- **다국어(i18n) 지원 추가**: 한국어(기본)/영어/일본어, 코드 수정 없이 언어 추가 가능한 구조

## 착수 전 실측 (2026-09-01, 전환 전 기준)

전체 진행률 ≈ 10~15%. 인프라와 DB 설계는 끝났고 애플리케이션 로직은 0줄.

완료된 것 (v1 기준, 재사용 가능):

- `database/init.sql` — v1 prompt.md §2의 6개 테이블 전부 (→ v2 DDL로 마이그레이션 필요, 아래 Step 0 참고)
- `docker-compose.yml` — mysql / redis / php-api / python-engine + healthcheck
- `deploy/`, `.github/workflows/` — rsync 배포, systemd 유닛, nginx conf, health 기반 배포 판정
- `.env.example` — Upbit / OpenAI / JWT / RateLimit 키 규약 (→ 다중 거래소 키/설정 추가 필요)

## Step 1 재평가 (전환에 따른 상태 변경)

| 항목 | 전환 전 상태 (v1) | 전환 후 필요 상태 (v2) |
| --- | --- | --- |
| 수집 대상 | Upbit 단일 | Binance/OKX/Bybit/Coinbase/Upbit 5개 |
| 지표 | RSI/MACD/MA/호가불균형 | + Bollinger/Stochastic/ADX/CCI |
| 판정 | "완료 (2026-09-01)" | **부분 재사용 — Upbit 어댑터는 그대로 두고, 거래소 어댑터 추상화 + 4개 거래소 추가 필요** |

**Step 1-a 완료 (2026-09-02, 9/3 예정분 선행)** — `market/exchange_registry.py`(5개 거래소 스펙 +
ccxt.pro 팩토리), `market/exchange_feed.py`(거래소별 독립 태스크·지수 백오프·이벤트 훅),
Redis `exchange:{code}:{symbol}:ticker|orderbook` 키. Binance/OKX 실수집 검증 완료. 테스트 47건.
시세 수집에는 거래소 API 키가 필요 없음을 5개 거래소 실측으로 확인했다(키는 Step 5 업비트 매매용만).
남은 것: Upbit 은 아직 전용 WebSocket 경로가 담당한다 — 지표를 거래소별로 돌리는 Step 1-b 에서 합친다.

Step 1은 "폐기 후 재작성"이 아니라 **"단일 거래소 구현을 다중 거래소 어댑터 구조로 리팩터링 + 4개 거래소 추가"** 로 처리한다. 기존 Upbit WebSocket/지표 코드는 `market/upbit_rest.py` 등에서 `market/exchange_registry.py` + `ccxt.pro` 기반 공통 인터페이스로 옮긴다.

## 스텝 목록 (v2)

| 완료 | 스텝 | 목표 | 착수 전 상태 | 완료일 |
| --- | --- | --- | --- | --- |
| [x] | **1** | 다중 거래소(ccxt) 수집 + 지표 엔진 확장 (`market/`, `indicators/`) | 부분 완료 — Upbit 단일 버전 존재, 4개 거래소 추가 + 지표 4종 추가 필요 | 2026-09-02 |
| [ ] | **2** | Consensus 계산 + RuleEngine + OpenAI 연동 + `ai_signals` 저장 (`strategy/`, `ai/`, `database/`) | 0% — 빈 패키지 | |
| [ ] | **3** | 유저별 TradingView 웹훅 연동, 멀티테넌트 PRO 기능 (`external/`, PHP `webhooks` API) | 0% — 신규 모듈 | |
| [ ] | **4** | 백테스팅 엔진, 신호검증용/업비트실전용 분리, 수수료·슬리피지 반영 (`backtest/`) | 0% — 빈 패키지 | |
| [ ] | **5** | 실거래 안전장치 + 매매 실행, Upbit 전용 (`trading/`) | 0% — 테이블만 있고 읽는 코드 없음 | |
| [ ] | **6** | PHP REST API (signals / market / backtest / safety, JWT, Rate Limit) | ~5% — 라우터·CORS 골격 + `/api/health`만 | |
| [ ] | **7** | 시그널 성과 추적 스케줄러 데몬 | 0% — systemd 유닛만 있고 실행할 코드 없음 | |
| [ ] | **8** | 테스트 · 구조화 로깅 · 알림 훅 | ~25% — Compose만 완료, 테스트 0건 | |
| [ ] | **9** | 대시보드 UI (신호 목록·상세·차트, 거래소 합의율 표시) | 0% — `api/public/index.html`은 정적 안내 페이지 | |
| [ ] | **10** | 다국어 지원 (ko/en/ja, 확장 가능 구조) | 0% — 신규, `lang/` 디렉터리 없음 | |
| [~] | **11** | 외부 데이터 아카이브 — 코인 뉴스 + 매크로 지표/캘린더 수집 (`external/`) | 0% — 신규, 설계만 (`docs/EXTERNAL_DATA.md`) | 11-a 완료 2026-09-02 |
| [ ] | **12** | 뉴스 메뉴 + 커뮤니티 (목록·상세·투표·댓글) | 0% — 신규 | |

## Step 0 — DB 마이그레이션 (신규, 착수 전 필수) — **완료 2026-09-02**

기존 `database/init.sql`(v1 DDL)이 이미 있으므로, 전체 재작성 대신 마이그레이션 스크립트를 추가한다.

**산출물**: `database/migrations/001_v2_multi_exchange.sql`, `database/migrate.sh`(러너),
`database/docker_initdb_migrate.sh`(신규 설치 시 자동 적용), `database/README.md`.
적용 이력은 `schema_migrations` 테이블로 추적한다. MySQL 8.0 컨테이너에서
기존 DB 경로·신규 설치 경로 양쪽으로 적용을 검증했다.

**미결(스펙 충돌, 결정 필요)**: 아래 3건은 `prompt.md` v2 §2 DDL과 기존 스키마가 어긋나
이번 마이그레이션에서 손대지 않았다. 결정 후 `002_*.sql`로 처리한다.
1. `ai_signals` 점수 컬럼 — v1은 `tech_score`/`ai_score`/`risk_score`/`final_score` 4개,
   v2 §2 DDL은 `score` 1개. Step 2 DoD가 Final Score 구성비를 요구하므로 4개를 유지했다.
2. `ai_signal_results` 구조 — v1은 horizon별 행(`UNIQUE(signal_id, horizon)`),
   v2 §2 DDL은 `price_after_5m/15m/1h` 가로 컬럼. Step 7 DoD의
   `INSERT ... ON DUPLICATE KEY UPDATE`는 v1 구조를 전제한다. v1 유지.
3. `trading_safety_state` — v2 §2 DDL에 없으나 Step 5 DoD가
   `kill_switch_active`/`max_position_size_krw`를 요구한다. v1 그대로 유지.

- **DoD**: `exchanges`, `user_webhooks`, `external_signals`(user_id/user_webhook_id 포함) 테이블 신규 생성. `users`에 `locale`(기본값 'ko') 컬럼 추가. `ai_signals`에 `entry_price_global`, `entry_price_upbit`, `bollinger_position`, `stochastic_k`, `stochastic_d`, `adx_val`, `cci_val`, `exchange_consensus_pct`, `data_sources_json` 컬럼 추가하고 기존 `market` 컬럼은 `symbol`로 정리(거래소 무관 심볼). `backtest_logs`에 `reference_exchange` 컬럼 추가. 마이그레이션은 `database/migrations/`에 순번 파일로 관리하며, 기존 데이터가 없는 상태이므로 롤백 스크립트는 생략 가능.

## 스텝별 완료 정의 (DoD, v2)

각 스텝의 상세 요구사항은 `prompt.md`(v2)의 해당 `[Step N]` 프롬프트를 원본으로 삼는다.
아래는 "끝났다"고 말하기 위한 최소 조건이다.

- **Step 1** — `.env`의 `EXCHANGES`(기본: binance,okx,bybit,coinbase,upbit)와 `MARKETS`(기본 5개: BTC/ETH/XRP/SOL/DOGE)로 지정한 조합의 ticker·orderbook(지원 거래소만)이 거래소별로 Redis에 실시간 적재되고, 거래소별 RSI(14)/MACD(12,26,9)/MA(5,20,60) 정배열/Bollinger(20,2)/Stochastic(14,3)/ADX(14)/CCI(20)/호가 불균형이 계산된다. 심볼별 거래량 가중 평균가/지표가 `global:{symbol}:*` 키로 별도 산출된다. 거래소별 연결이 끊기면 해당 거래소만 지수 백오프로 재접속하고 다른 거래소 수집에는 영향이 없다.
- **Step 2** — `S_Consensus`가 prompt.md §3.2대로 산출되고, 유효 거래소 3개 미만이면 HOLD로 강등된다. `S_Tech`가 §3.3 배점표대로 산출되고 `min(sum, 100)`으로 clamp된다. Tech 70 이상/30 이하 **그리고** Consensus 50% 이상일 때만 OpenAI를 호출한다. Redis TTL 키로 중복 호출을 막는다. Final Score(Tech 45%+Consensus 20%+AI 20%+Risk 15%)가 계산되고 확률 합계가 95~105를 벗어나면 HOLD로 강등된다. 결과가 `ai_signals`(신규 컬럼 포함)에 저장되고 `channel:signals`로 publish된다.
- **Step 3** *(PRO 기능, 멀티테넌트)* — 로그인 유저가 `POST /api/v1/webhooks/tradingview`로 고유 웹훅 URL(토큰 포함)을 발급받을 수 있다. `POST /webhook/tv/{token}`이 존재하지 않거나 폐기된 토큰이면 404를 반환한다. 수신 payload가 해당 유저의 `user_id`/`user_webhook_id`와 함께 `external_signals`에 저장된다. 동일 `symbol`의 최근 `ai_signals`가 있으면 `linked_signal_id`로 참고 연결만 하고 점수를 소급 변경하지 않는다. 유저 A의 신호가 유저 B 대시보드에 노출되지 않음을 검증하는 테스트가 있다. 이 기능을 비활성화하거나 유저가 미사용이어도 Step 1·2가 정상 동작한다.
- **Step 4** — 신호검증용(`reference_exchange='GLOBAL_CONSENSUS'`)과 업비트 실전용(`reference_exchange='upbit'`) 백테스트가 분리되어 실행되고, 결과가 하나의 지표로 합쳐지지 않는다. 업비트 실전용에는 수수료 0.05%(매수·매도 각각), 슬리피지 0.05%가 반영된다. 총수익률/승률/거래수/손익비/MDD를 내고 `backtest_logs`에 저장한다. 재현성 테스트 1개 이상.
- **Step 5** — 기본 모드가 **PAPER**다. LIVE 전환은 명시적 조작 + 감사 로그를 남긴다. 일일 손실 한도 초과 시 `kill_switch_active=1`로 신규 주문을 자동 차단하고, 수동 해제 전까지 유지한다. 단일 주문이 `max_position_size_krw`를 넘지 않는다. 기동 시 Upbit 키에 출금 권한이 없는지 검증한다. 매매 대상은 업비트 상장 종목의 `ai_signals`로 한정되고, 체결/잔고 조회는 반드시 업비트 API로만 수행한다.
- **Step 6** — `GET /signals/latest`(페이지네이션, `exchange_consensus_pct`/`data_sources_json` 포함), `GET /signals/strong`, `GET /market/summary`(글로벌 가중 평균가 포함), `POST /backtest/run`(`reference_exchange` 파라미터), `GET|PATCH /safety/state`가 동작한다. 공통 에러 포맷, JWT 인증, 분당 60회 Rate Limit, 전 쿼리 Prepared Statement.
- **Step 7** — 5m/15m/1h horizon 미평가 건만 골라 `INSERT ... ON DUPLICATE KEY UPDATE`로 기록한다. 글로벌 가중 평균가 기준 BUY 계열 +0.2% 초과/SELL 계열 -0.2% 미만이면 `is_accurate=1`, HOLD는 제외. Redis 분산 락으로 중복 실행에도 안전하다.
- **Step 8** — RuleEngine clamp(100 상한), Consensus 계산, 백테스트, 성과 추적에 pytest 단위 테스트가 있다. `ci.yml`의 "exit 5 우회"를 제거한다. Python·PHP 양쪽 JSON 구조화 로깅과 에러 알림 훅을 넣는다.
- **Step 9** — 브라우저에서 신호 목록·상세와 거래소 합의율(Consensus %)을 보고, 마켓별 점수 추이 차트를 확인할 수 있다. Step 6의 API만 호출하며 DB에 직접 붙지 않는다. 모든 화면 하단에 prompt.md §7의 법적 고지(트레이딩뷰 공식 데이터가 아니라는 문구 포함)를 노출한다. (prompt.md의 Step 1~7 범위 밖. 9/2 논의에서 추가 합의.)
- **Step 10** — `SUPPORTED_LOCALES` 설정에 언어 코드를 추가하고 `lang/{code}.json` 파일 하나만 채우면 코드 수정 없이 새 언어가 노출된다. `ko/en/ja` 3개 언어로 UI 전체(메뉴/버튼/에러 메시지/법적 고지)가 번역되어 있다. 유저가 마이페이지에서 언어를 변경하면 `users.locale`이 갱신되고 다음 로그인부터 유지된다. AI 신호의 `reasons`/`risks`를 기준 언어와 다른 `locale`로 볼 때 자동 번역되며 "AI 자동 번역" 배지가 표시된다. 법적 고지는 언어별 고정 문구이며 실시간 번역을 거치지 않는다.

## Step 11·12 — 외부 데이터 아카이브 (2026-09-02 추가 합의)

`prompt.md` v2 범위 밖의 확장. 상세 설계는 **`docs/EXTERNAL_DATA.md`**.

- **Step 11 (수집)** — 코인 뉴스 RSS + 매크로 시계열(FRED/美재무부) + 경제 캘린더를
  아카이브한다. **신호 점수에는 반영하지 않는다.** 목적은 아카이브 축적이다 —
  과거 데이터는 지금 모으지 않으면 소급 확보가 안 된다.
  - DoD: 뉴스/매크로/캘린더가 각각의 테이블에 적재되고, 모든 레코드가
    "사건 시각"과 "우리가 본 시각"을 분리 보관한다. 매크로는 개정(revision)을 덮어쓰지 않는다.
    `external/point_in_time.py`의 `as_of(t)`가 t 시점에 알 수 있었던 것만 돌려주고,
    이를 검증하는 테스트가 있다. 비공식 스크래핑을 쓰지 않는다.
- **Step 12 (뉴스 메뉴 + 커뮤니티)** — 기사 목록·상세(원문 링크 아웃)·상승/하락 투표·댓글.
  Step 6 API와 Step 9 UI 위에 얹는다.
  - DoD: 원문 본문을 재배포하지 않고 제목·요약·링크만 노출한다. 유저 투표가
    `article_sentiments`에 `classifier='crowd'`로 집계되어 AI 분류와 나란히 비교된다.
    유저는 1기사 1표이며 변경할 수 있다.

**편입 판단은 Step 11·12가 아니라 데이터가 쌓인 뒤에 한다.** 상관관계가 없으면
점수에 넣지 않는 것도 결과다. 넣기로 하면 `Final Score` 재배분(Tech 45/Consensus 20/
AI 20/Risk 15)이 필요하므로 `prompt.md` §3.3 개정이 선행되어야 한다.

**사용자 액션 필요**: FRED 무료 API 키 발급 (https://fred.stlouisfed.org/docs/api/api_key.html).
연준금리·CPI·유가·달러지수와 과거 시점 데이터(ALFRED)를 한 키로 커버한다.

## 확정 일정 (v2, 주말 포함)

> 9/1에 v1 기준 Step 1-a·1-b를 끝냈으나 9/2 방향 전환(다중 거래소 확장 + 유저별 트레이딩뷰 연동 + 다국어)으로 **종료일이 9/10 → 9/19로 늘어난다.**
> 9/2에 Step 0·1-a·1-b를 모두 끝내 **Step 1 전체가 완료됐고, 이후 일정을 이틀 당겼다. 종료일 9/19 → 9/17.**

| 날짜 | 진행 |
| --- | --- |
| 9/1 (화) | ~~[v1] Upbit WebSocket 수집 + Redis 캐싱~~ 완료 (다중 거래소 어댑터로 재사용) |
| 9/1 (화) | ~~[v1] 지표 계산(RSI/MACD/MA/호가불균형) + 재접속 백오프~~ 완료 (재사용) |
| 9/2 (수) | ~~Step 0 — DB 마이그레이션 (exchanges, user_webhooks, external_signals, users.locale, ai_signals 컬럼 추가)~~ 완료 |
| 9/2 (수) | ~~Step 1-a — ccxt 거래소 어댑터 추상화 + Binance/OKX 추가~~ 완료 (9/3 예정분 선행) |
| 9/2 (수) | ~~Step 1-b — Bybit/Coinbase 추가 + 지표 4종 + 글로벌 가중 평균~~ 완료 (9/3 예정분 선행) |
| 9/3 (목) | Step 2-a — Consensus 계산 + RuleEngine 재작성 |
| 9/4 (금) | Step 2-b — OpenAI 연동 + `ai_signals` 저장/publish |
| 9/5 (토) | Step 3-a — 유저 웹훅 발급/재발급 API (PHP) |
| 9/6 (일) | Step 3-b — 멀티테넌트 웹훅 수신 엔드포인트 (Python) + 유저 격리 테스트 |
| 9/7 (월) | Step 4-a — 백테스팅 엔진(신호검증용) |
| 9/8 (화) | Step 4-b — 백테스팅 엔진(업비트 실전용, 수수료/슬리피지) |
| 9/9 (수) | Step 5 — 실거래 안전장치·매매 실행(업비트 전용) |
| 9/10 (목) | Step 6-a — 엔드포인트 5종 + 공통 에러 포맷 |
| 9/11 (금) | Step 6-b — JWT 인증 + Rate Limit |
| 9/12 (토) | Step 7 — 성과 추적 스케줄러 |
| 9/13 (일) | Step 8 — 테스트 · 로깅 · 알림 훅 |
| 9/14 (월) | Step 9-a — 신호 목록·상세 화면 (합의율 표시 포함) |
| 9/15 (화) | Step 9-b — 점수 추이 차트 + 법적 고지 |
| 9/16 (수) | Step 10-a — 언어팩 인프라 (lang/*.json, locale 설정, 유저 언어 변경) |
| 9/17 (목) | Step 10-b — AI 문구 실시간 번역/캐싱 + 법적 고지 다국어 검수 반영 |
| 9/2 (수) | ~~Step 11-a — 뉴스 RSS 수집 + 감성 분류 (신호 미반영)~~ 완료 (9/18 예정분 선행) |
| 9/19 (토) | Step 11-b — 매크로 시계열·캘린더 + `as_of(t)` 조회 계층 |
| 9/20 (일) | Step 12 — 뉴스 메뉴 + 커뮤니티(투표·댓글) |