# [PRD & Dev Spec] AI Trading Platform (가칭: AI Trading)

본 문서는 **Python 트레이딩/AI 분석 엔진**과 **PHP/MySQL 기반 SaaS 웹 서비스**로 구성된 실시간 가상자산 분석 및 신호 제공 플랫폼의 상세 기술 명세서입니다.

AI 코딩 에이전트(Cursor, Claude Code, GitHub Copilot 등)에 순차적으로 투입해 구현할 수 있도록 설계되었습니다.

> ⚠️ **개정 노트**: 이 버전은 기존 초안에서 다음을 수정/보강했습니다.
> - 기술 점수(S_Tech) 산식이 100점을 초과하던 버그 수정
> - 존재하지 않는 모델명 → 실제 계약 시점에 확정할 플레이스홀더로 변경
> - **실거래 안전장치(손실 한도, 킬스위치, 페이퍼 트레이딩 모드) 신규 추가**
> - 시그널 결과 추적의 멱등성(중복 방지) 처리 추가
> - API 에러 포맷, 페이지네이션, Rate Limit 정책 추가
> - 테스트/모니터링/배포 섹션 신규 추가

---

## 1. 시스템 아키텍처 및 역할 분담

```
                 ┌──────────────────────────┐
                 │  Upbit API (REST / WS)   │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │   Python Engine (Daemon) │
                 └────────────┬─────────────┘
                              │ Real-time Push / State Sync
                              ▼
                 ┌──────────────────────────┐
                 │       Redis Cache        │
                 └────────────┬─────────────┘
                              │ Persistent Data / Aggregates
                              ▼
                 ┌──────────────────────────┐
                 │      MySQL Database      │
                 └────────────┬─────────────┘
                              │ Read / Write
                              ▼
                 ┌──────────────────────────┐
                 │   PHP API Server (SaaS)  │
                 └────────────┬─────────────┘
                              │ HTTPS / REST API
                              ▼
                 ┌──────────────────────────┐
                 │   Client (Web / Mobile)  │
                 └──────────────────────────┘
```

- **Python Engine**: Upbit WebSocket 수집, 기술적 지표 계산, Rule Engine 평가, OpenAI API 연동, 가상/실제 매매 실행, Redis 캐싱 및 MySQL 영구 저장
- **Redis**: Real-time Ticks, Orderbook, In-Memory Indicators, Pub/Sub 이벤트 버스
- **MySQL**: 사용자, 구독, AI 분석 신호(`ai_signals`), 신호 결과 검증(`ai_signal_results`), 백테스트 이력
- **PHP Web/API**: 회원가입/인증, PG 결제 연동, AI Signal Dashboard REST API, 백테스트 실행 요청 전달

---

## 2. DB 스키마 (MySQL DDL)

```sql
CREATE DATABASE IF NOT EXISTS `ai_trading` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `ai_trading`;

-- 1. 사용자 테이블
CREATE TABLE `users` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `email` VARCHAR(191) NOT NULL UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `name` VARCHAR(50) NOT NULL,
    `role` ENUM('user', 'admin') DEFAULT 'user',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2. 구독 정보 테이블
CREATE TABLE `subscriptions` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `plan` ENUM('free', 'basic', 'pro') DEFAULT 'free',
    `status` ENUM('active', 'canceled', 'expired') DEFAULT 'active',
    `starts_at` TIMESTAMP NOT NULL,
    `ends_at` TIMESTAMP NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. AI 시그널 테이블 (핵심)
CREATE TABLE `ai_signals` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `market` VARCHAR(20) NOT NULL,             -- 예: KRW-BTC
    `timeframe` VARCHAR(10) NOT NULL,          -- 5m, 15m, 1h
    `signal_type` ENUM('STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL') NOT NULL,
    `tech_score` TINYINT UNSIGNED NOT NULL,    -- 0~100, S_Tech
    `ai_score` TINYINT UNSIGNED NULL,          -- 0~100, S_AI (OpenAI 미호출 시 NULL)
    `risk_score` TINYINT UNSIGNED NOT NULL,    -- 0~100, S_Risk
    `final_score` TINYINT UNSIGNED NOT NULL,   -- 합성 최종 점수, 0~100
    `up_prob` DECIMAL(5, 2) NOT NULL,
    `sideways_prob` DECIMAL(5, 2) NOT NULL,
    `down_prob` DECIMAL(5, 2) NOT NULL,
    `entry_price` DECIMAL(18, 8) NOT NULL,
    `rsi_val` DECIMAL(6, 2) NULL,
    `macd_val` DECIMAL(12, 4) NULL,
    `volume_change_pct` DECIMAL(8, 2) NULL,
    `reasons_json` JSON NOT NULL,
    `risks_json` JSON NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_market_created` (`market`, `created_at`),
    INDEX `idx_score` (`final_score`)
) ENGINE=InnoDB;

-- 4. AI 시그널 성과 추적 테이블
CREATE TABLE `ai_signal_results` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `signal_id` BIGINT UNSIGNED NOT NULL,
    `horizon` ENUM('5m', '15m', '1h') NOT NULL, -- 어떤 시점 평가인지 명시 (멱등성 키)
    `price_entry` DECIMAL(18, 8) NOT NULL,
    `price_after` DECIMAL(18, 8) NULL,
    `return_pct` DECIMAL(6, 2) NULL,
    `is_accurate` TINYINT(1) DEFAULT NULL,
    `evaluated_at` TIMESTAMP NULL,
    UNIQUE KEY `uq_signal_horizon` (`signal_id`, `horizon`), -- 중복 평가 방지
    FOREIGN KEY (`signal_id`) REFERENCES `ai_signals`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 5. 백테스트 이력 테이블
CREATE TABLE `backtest_logs` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `market` VARCHAR(20) NOT NULL,
    `strategy_name` VARCHAR(50) NOT NULL,
    `start_date` DATE NOT NULL,
    `end_date` DATE NOT NULL,
    `initial_capital` DECIMAL(18, 2) NOT NULL,
    `final_capital` DECIMAL(18, 2) NOT NULL,
    `total_return_pct` DECIMAL(8, 2) NOT NULL,
    `win_rate` DECIMAL(5, 2) NOT NULL,
    `mdd` DECIMAL(5, 2) NOT NULL,
    `total_trades` INT UNSIGNED NOT NULL,
    `params_json` JSON NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 6. 실거래 리스크 관리 테이블 (신규)
CREATE TABLE `trading_safety_state` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `mode` ENUM('PAPER', 'LIVE') NOT NULL DEFAULT 'PAPER', -- 기본값은 반드시 모의투자
    `daily_loss_limit_pct` DECIMAL(5, 2) NOT NULL DEFAULT 3.00,
    `max_position_size_krw` DECIMAL(18, 2) NOT NULL,
    `kill_switch_active` TINYINT(1) NOT NULL DEFAULT 0, -- 1이면 모든 신규 주문 즉시 중단
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;
```

---

## 3. 핵심 자료구조 및 알고리즘 명세

### 3.1 Python 데이터 파이프라인 자료구조 (Circular Buffer & Redis)

- **WebSocket Ticks Buffer**: 초당 수십 건 수집 처리를 위해 Python `collections.deque(maxlen=200)` 사용
- **Redis Key Structure**:
  - `market:KRW-BTC:ticker` → String (JSON: 현재가, 체결량, 변동률)
  - `market:KRW-BTC:orderbook` → String (JSON: 매수/매도 호가 잔량 비율)
  - `market:KRW-BTC:candles:5m` → List (최근 100개 캔들 데이터)
  - `market:KRW-BTC:last_signal_at` → String (마지막 OpenAI 호출 시각, TTL로 호출 빈도 제어)

### 3.2 복합 스코어링 알고리즘 (Hybrid Score) — 수정본

기존 산식은 `S_Tech`의 하위 가점(최대 200점)이 100점 만점 가정과 충돌했습니다. 아래와 같이 **정규화**합니다.

$$\text{Final Score} = (S_{\text{Tech}} \times 0.6) + (S_{\text{AI}} \times 0.2) + (S_{\text{Risk}} \times 0.2)$$

**$S_{\text{Tech}}$ (기술적 지표 점수, 0~100점, 정규화 필수)**

각 항목은 아래 배점의 "일부"이며, 합계가 100점을 넘지 않도록 가중치를 나눕니다:

| 항목 | 배점 | 판정 기준 |
|---|---|---|
| RSI(14) | 40점 | 30 이하: 40점, 70 이상: 0점, 그 사이는 선형 보간 |
| MACD Golden Cross | 15점 | 발생 시 15점, 아니면 0점 |
| 이동평균 정배열 (MA5>MA20>MA60) | 15점 | 정배열 15점 / 역배열 0점 / 혼조 7점 |
| 거래량 급증 (전 시간 대비 +30% 이상) | 15점 | 조건 충족 시 15점, 비례 감점 가능 |
| 호가 잔량 우위 (Imbalance > 15%) | 15점 | 조건 충족 시 15점, 비례 감점 가능 |

→ 합계 = 40+15+15+15+15 = **100점 상한**, 코드에서도 `min(sum, 100)`으로 clamp 처리할 것.

- **$S_{\text{AI}}$**: OpenAI Structured Output의 `ai_score` (0~100)
- **$S_{\text{Risk}}$**: 100 − (ATR 기반 변동성 과열 패널티). ATR이 최근 20봉 평균 대비 150% 이상이면 감점폭 확대

---

## 4. AI Structured Output 스키마 (OpenAI API 연동)

> ⚠️ 원본에 있던 모델명(`gpt-5.6-luna`)은 실재하지 않는 이름입니다. **실제 구현 시점에 사용 가능한 모델명을 최신 API 문서에서 확인 후 지정**하세요. 아래는 플레이스홀더입니다.

### Prompt 구조

```
System: You are an expert quantitative crypto analysis assistant. Analyze the
pre-calculated technical indicators and orderbook metrics for {market}.
Do NOT fabricate raw price data — only use the values provided.
Evaluate short-term (5m, 15m) probability and return strict JSON format only.
This output is informational and does not constitute financial advice.

User Data:
{
  "market": "KRW-BTC",
  "current_price": 154200000,
  "rsi_14": 52.31,
  "macd_status": "GOLDEN_CROSS",
  "volume_surge_pct": 37.2,
  "orderbook_imbalance": 21.4,
  "ma_trend": "BULLISH"
}
```

### Response JSON Schema

```json
{
  "type": "object",
  "properties": {
    "signal": {
      "type": "string",
      "enum": ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]
    },
    "ai_score": { "type": "integer", "minimum": 0, "maximum": 100 },
    "probabilities": {
      "type": "object",
      "properties": {
        "up": { "type": "number" },
        "sideways": { "type": "number" },
        "down": { "type": "number" }
      },
      "required": ["up", "sideways", "down"]
    },
    "reasons": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 2,
      "maxItems": 5
    },
    "risks": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 3
    }
  },
  "required": ["signal", "ai_score", "probabilities", "reasons", "risks"]
}
```

`up + sideways + down`은 100에 근사해야 하며, 파싱 후 코드에서 합계가 95~105 범위를 벗어나면 재요청하거나 HOLD로 강등 처리할 것.

---

## 5. 단계별 구현 및 개발 로드맵

AI 코더에게는 아래 **[Step 1] ~ [Step 7]** 프롬프트를 한 단계씩 순차적으로 제공합니다. (기존 5단계에 안전장치·테스트 단계를 추가해 7단계로 확장)

---

### [Step 1] Python Upbit WebSocket & Indicator Engine 구축

```
[Prompt for AI Coder]

우리는 가상자산 분석 SaaS를 구축 중입니다. First Step으로 Python 기반 Upbit
WebSocket 수집 및 기술적 지표 계산 모듈을 작성해주세요.

[요구사항]
1. `websockets` 및 `asyncio` 라이브러리를 사용하여 Upbit WebSocket API에 접속하세요.
2. 수집 대상: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE
3. 체결(ticker) 및 호가(orderbook) 실시간 데이터를 수집하여 Redis에 즉시 캐싱하세요.
4. `pandas_ta` 또는 `ta` 라이브러리로 최근 100개 캔들 기준 다음 지표를 실시간 계산하는
   클래스를 만드세요:
   - RSI(14)
   - MACD(12, 26, 9) & Signal Cross 여부
   - 이동평균(MA5, MA20, MA60) 정배열/역배열 판단
   - 호가 잔량 비율(Orderbook Imbalance)
5. 지표가 업데이트되면 룰 엔진 평가 함수를 호출하는 이벤트 구조를 설계하세요.
6. WebSocket 연결이 끊기면 지수 백오프(exponential backoff)로 재접속하는 로직을
   포함하세요. 재접속 시도, 실패, 성공 이벤트를 모두 로그로 남기세요.
```

---

### [Step 2] Rule Engine + OpenAI API 연동 및 DB 저장

```
[Prompt for AI Coder]

Python 프로젝트에 룰 기반 평가 알고리즘과 OpenAI Structured Output API를 통합하세요.

[요구사항]
1. 기술적 지표를 입력받아 0~100점 사이로 clamp된 `Technical Score`를 산출하는
   `RuleEngine` 클래스를 만드세요. (본 문서 3.2절의 배점표를 그대로 구현)
2. `Technical Score`가 70점 이상이거나 30점 이하일 때만 OpenAI API를 호출하도록
   필터링하세요. 추가로 동일 market에 대해 최근 N분 이내 호출 이력이 있으면
   Redis TTL 키로 재호출을 막아 비용을 통제하세요.
3. OpenAI API 호출 시 Pydantic 모델로 JSON Schema를 강제하고, 확률 합계
   검증(95~105 범위) 실패 시 HOLD로 강등 처리하는 로직을 넣으세요.
4. 지표 점수(60%) + AI 점수(20%) + 리스크 점수(20%)를 합성한 최종 Score를
   계산하고, 각 하위 점수를 모두 별도 컬럼에 저장하세요(디버깅/검증용).
5. 계산된 최종 신호를 MySQL `ai_signals`에 저장하고, Redis Pub/Sub 채널
   (`channel:signals`)로 Publish하세요.
6. OpenAI API 호출 실패(타임아웃, 429 등) 시 재시도 정책과 폴백(기술 점수만으로
   신호 확정)을 구현하세요.
```

---

### [Step 3] 백테스팅 엔진 구현 (수수료/슬리피지 포함)

```
[Prompt for AI Coder]

Python으로 과거 업비트 OHLCV 데이터를 기반으로 전략 성과를 측정하는
백테스팅 모듈을 개발하세요.

[요구사항]
1. Upbit REST API로 과거 1분/5분/1시간 캔들 데이터를 수집/파싱하세요.
2. 백테스트 매매 조건:
   - 매수: AI Combined Score >= 80
   - 매도: AI Combined Score <= 30 또는 익절(+1.5%), 손절(-1.0%) 조건 달성 시
3. 필수 반영 파라미터:
   - 업비트 거래 수수료: 0.05% (매수/매도 각각 적용)
   - 슬리피지: 0.05% 적용
4. 출력 지표: 총 수익률(%), 승률(%), Total Trades, 평균 수익/손실 비율,
   MDD(Maximum Drawdown %)
5. 실행 결과를 MySQL `backtest_logs`에 저장하는 파이프라인을 작성하세요.
6. 동일 파라미터로 재실행 시 동일 결과가 나오는지 확인하는 재현성(reproducibility)
   테스트를 최소 1개 포함하세요.
```

---

### [Step 4] 실거래 안전장치 및 매매 실행 모듈 (신규 단계)

```
[Prompt for AI Coder]

실제 자금이 오갈 수 있는 매매 실행 모듈을 작성하되, 아래 안전장치를
최우선으로 구현하세요.

[요구사항]
1. `trading_safety_state` 테이블을 참조하여 사용자별 `mode`가 'PAPER'인 경우
   실제 주문 API를 호출하지 않고 가상 체결만 기록하세요. 기본값은 반드시 PAPER.
2. 'LIVE' 모드 진입은 사용자가 웹에서 명시적으로 전환한 경우에만 허용하고,
   전환 시점을 별도 감사 로그(audit log)에 기록하세요.
3. 일일 손실 한도(`daily_loss_limit_pct`)를 초과하면 해당 계정의 신규 주문을
   자동 차단하고 `kill_switch_active`를 1로 설정하세요.
4. 킬스위치가 활성화된 계정은 관리자 또는 사용자 본인이 수동으로 해제하기
   전까지 어떤 신규 주문도 실행하지 않도록 하세요.
5. 단일 주문 금액이 `max_position_size_krw`를 초과하지 않도록 사전 검증하세요.
6. Upbit API 키는 출금 권한을 제외하고 조회/매매 권한만 부여된 키인지
   애플리케이션 구동 시 검증하세요 (권한 조회 API 응답 확인).
```

---

### [Step 5] PHP REST API & Dashboard Backend

```
[Prompt for AI Coder]

PHP(순수 PHP 또는 Laravel/CodeIgniter 스타일)를 사용하여 웹 프론트엔드가
사용할 RESTful API를 작성해주세요.

[요구사항]
1. DB 접속 정보는 `.env` 파일에서 읽어오도록 처리하세요.
2. API Endpoints:
   - `GET /api/v1/signals/latest?page=&limit=`: 최근 신호 목록 (페이지네이션 필수)
   - `GET /api/v1/signals/strong`: final_score 80 이상 신호
   - `GET /api/v1/market/summary`: Redis 실시간 시세 및 점수 캐시
   - `POST /api/v1/backtest/run`: 백테스트 요청을 백그라운드 Python 프로세스로 전달
   - `GET /api/v1/safety/state`, `PATCH /api/v1/safety/state`: 사용자별
     `trading_safety_state` 조회/수정 (LIVE 전환은 재인증 요구)
3. 모든 응답은 아래와 같은 공통 에러 포맷을 따르세요:
   ```json
   { "success": false, "error": { "code": "STRING_CODE", "message": "..." } }
   ```
4. CORS 처리, JWT 기반 인증 미들웨어, 그리고 IP/계정 단위 Rate Limit
   (예: 분당 60회)을 구현하세요.
5. 모든 사용자 입력은 Prepared Statement로 처리하여 SQL Injection을 방지하세요.
```

---

### [Step 6] 시그널 성과 자동 추적 스케줄러 (Daemon)

```
[Prompt for AI Coder]

과거에 생성된 AI 시그널이 실제로 적중했는지 자동 평가하는 Python
크론/스케줄러 데몬을 작성하세요.

[요구사항]
1. `ai_signals`에서 생성된 지 5분/15분/1시간이 지난 시그널 중
   `ai_signal_results`에 해당 `horizon`으로 아직 기록되지 않은 건만 조회하세요.
   (`UNIQUE(signal_id, horizon)` 제약을 활용해 중복 실행 시에도 안전하게
   INSERT ... ON DUPLICATE KEY UPDATE로 처리)
2. 시그널 발생 시점의 `entry_price` 대비 해당 시점의 실제 업비트 가격을 조회하세요.
3. 수익률(`return_pct`)을 계산하세요.
4. 판단 로직:
   - `BUY` 계열 시그널: 수익률 > +0.2% 이면 `is_accurate = 1`, 아니면 0
   - `SELL` 계열 시그널: 수익률 < -0.2% 이면 `is_accurate = 1`, 아니면 0
   - `HOLD`: 평가 대상에서 제외
5. 이 데몬 자체가 중복 실행되어도(예: 크론이 겹쳐 실행) 데이터 무결성이
   깨지지 않도록 분산 락(Redis 기반)을 적용하세요.
```

---

### [Step 7] 테스트, 모니터링, 배포 (신규 단계)

```
[Prompt for AI Coder]

프로덕션 투입 전 최소한의 테스트/관측 가능성(observability)을 구축하세요.

[요구사항]
1. RuleEngine 점수 산식, 백테스트 엔진, 성과 추적 스케줄러에 대해 단위
   테스트(pytest)를 작성하세요. 특히 3.2절 배점표의 clamp(100 상한) 로직을
   반드시 테스트하세요.
2. Python 데몬과 PHP API 각각에 구조화 로깅(JSON 로그)을 적용하고,
   에러 발생 시 알림(Slack/Webhook 등)을 보낼 수 있는 훅을 마련하세요.
3. `kill_switch_active` 상태 변경, LIVE 모드 전환, OpenAI API 실패율에 대한
   최소한의 대시보드/알림 기준을 정의하세요.
4. Docker Compose로 Python Engine, Redis, MySQL, PHP 서버를 한 번에 기동할 수
   있는 로컬 개발 환경을 구성하세요.
```

---

## 6. 개발 시 보안 및 운용 필수 체크리스트

1. **API Key 분리 및 보안**
   - Upbit API Key는 PHP 웹 루트 외부, Python 실행 환경의 `.env`에만 보관
   - Upbit API Key는 **IP 제한(서버 고정 IP)** 필수, **출금 권한 제외**(조회/매매 권한만 부여)

2. **Rate Limit 관리**
   - Upbit WebSocket 재접속(Reconnection) 및 Backoff 로직 구현
   - Upbit REST API 초당 요청 수 초과 방지를 위한 Redis 기반 Throttling

3. **실거래 리스크 관리 (신규)**
   - 기본 모드는 항상 PAPER(모의투자)이며, LIVE 전환은 사용자의 명시적 조작 + 재인증 필요
   - 일일 손실 한도, 단일 주문 한도, 킬스위치는 서버 사이드에서 강제하며 클라이언트 값을 신뢰하지 않음
   - LIVE 모드 전환 및 킬스위치 발동 이력은 감사 로그로 별도 보관

4. **법적 고지(Disclaimer)**
   모든 Dashboard 및 Signal 화면 하단에 아래 문구 필수 노출:

   > "본 서비스에서 제공하는 분석 결과 및 AI 신호는 투자 참고용 데이터이며, 수익을 보장하지 않습니다. 모든 투자의 최종 책임은 본인에게 있습니다."

5. **개인정보/결제 처리**
   - PG 연동 시 카드번호 등 민감정보는 서버에 저장하지 않고 PG사 토큰만 보관
   - 비밀번호는 반드시 salt 포함 해시(bcrypt/argon2)로 저장