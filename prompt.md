제시해주신 기획안을 바탕으로 **AI 코딩 에이전트(Cursor, Claude Code, GitHub Copilot 등)에 즉시 투입하여 단계별로 구현할 수 있는 최상위 수준의 PRD 및 시스템 설계 명세서**로 정리했습니다.

이 문서를 AI 코더에게 전달하면 복잡한 컨텍스트 해석 없이 순차적으로 완전한 코드를 생성해낼 수 있습니다.

---

# [PRD & Dev Spec] AI Trading Platform (가칭: AI Trading)

본 문서는 **Python 트레이딩/AI 분석 엔진**과 **PHP/MySQL 기반 SaaS 웹 서비스**로 구성된 실시간 가상자산 분석 및 신호 제공 플랫폼의 상세 기술 명세서입니다.

---

## 1. 시스템 아키텍처 및 역할 분담

```
                   [ Upbit API (REST / WebSocket) ]
                                  │
                                  ▼
                        ┌──────────────────┐
                        │  Python Engine   │ (Daemon)
                        └────────┬─────────┘
                                 │ Real-time Push / State Sync
                        ┌────────┴─────────┐
                        │   Redis Cache    │
                        └────────┬─────────┘
                                 │ Persistent Data / Aggregates
                        ┌────────┴─────────┐
                        │  MySQL Database  │
                        └────────┬─────────┘
                                 │ Read / Write
                        ┌────────┴─────────┐
                        │   PHP API Server │ (Web SaaS)
                        └────────┬─────────┘
                                 │ HTTPS / REST API
                        ┌────────┴─────────┘
                        │ Client (Web/Mobile)
                        └──────────────────┘

```

* **Python Engine**: Upbit WebSocket 수집, 기술적 지표 계산, Rule Engine 평가, OpenAI API 연동, 가상/실제 매매 실행, Redis 캐싱 및 MySQL 영구 저장
* **Redis**: Real-time Ticks, Orderbook, In-Memory Indicators, Pub/Sub 이벤트 버스
* **MySQL**: 사용자, 구독, AI 분석 신호(`ai_signals`), 신호 결과 검증(`ai_signal_results`), 백테스트 이력
* **PHP Web/API**: 회원가입/인증, PG 결제 연동, AI Signal Dashboard REST API, 백테스트 실행 요청 전달

---

## 2. DB 스키마 (MySQL DDL)

AI 코더가 그대로 실행하여 테이프를 생성할 수 있는 완벽한 DDL입니다.

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
    `market` VARCHAR(20) NOT NULL, -- 예: KRW-BTC
    `timeframe` VARCHAR(10) NOT NULL, -- 5m, 15m, 1h
    `signal_type` ENUM('STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL') NOT NULL,
    `score` TINYINT UNSIGNED NOT NULL, -- 0~100
    `up_prob` DECIMAL(5, 2) NOT NULL, -- 상승 확률 (%)
    `sideways_prob` DECIMAL(5, 2) NOT NULL,
    `down_prob` DECIMAL(5, 2) NOT NULL,
    `entry_price` DECIMAL(18, 8) NOT NULL,
    `rsi_val` DECIMAL(6, 2) NULL,
    `macd_val` DECIMAL(12, 4) NULL,
    `volume_change_pct` DECIMAL(8, 2) NULL,
    `reasons_json` JSON NOT NULL, -- 주요 근거
    `risks_json` JSON NOT NULL, -- 위험 요소
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_market_created` (`market`, `created_at`),
    INDEX `idx_score` (`score`)
) ENGINE=InnoDB;

-- 4. AI 시그널 성과 추적 테이블 (추후 적중률 검증용)
CREATE TABLE `ai_signal_results` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `signal_id` BIGINT UNSIGNED NOT NULL,
    `price_entry` DECIMAL(18, 8) NOT NULL,
    `price_after_5m` DECIMAL(18, 8) NULL,
    `price_after_15m` DECIMAL(18, 8) NULL,
    `price_after_1h` DECIMAL(18, 8) NULL,
    `return_5m` DECIMAL(6, 2) NULL,
    `return_15m` DECIMAL(6, 2) NULL,
    `return_1h` DECIMAL(6, 2) NULL,
    `is_accurate` TINYINT(1) DEFAULT NULL, -- 적중 여부
    `evaluated_at` TIMESTAMP NULL,
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
    `mdd` DECIMAL(5, 2) NOT NULL, -- Maximum Drawdown
    `total_trades` INT UNSIGNED NOT NULL,
    `params_json` JSON NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

```

---

## 3. 핵심 자료구조 및 알고리즘 명세

### 3.1 Python 데이터 파이프라인 자료구조 (Circular Buffer & Redis)

* **WebSocket Ticks Buffer**: 메모리 효율 및 초당 수십 건 수집 처리를 위해 Python `collections.deque(maxlen=200)` 사용
* **Redis Key Structure**:
* `market:KRW-BTC:ticker` -> String (JSON Data: 현재가, 체결량, 변동률)
* `market:KRW-BTC:orderbook` -> String (JSON Data: 매수/매도 호가 잔량 비율)
* `market:KRW-BTC:candles:5m` -> List (최초 100개 캔들 데이터)



### 3.2 복합 스코어링 알고리즘 (Hybrid Score)

단순 LLM 판단의 오류를 줄이기 위한 가중 수식 알고리즘:

$$\text{Final Score} = (S_{\text{Tech}} \times 0.6) + (S_{\text{AI}} \times 0.2) + (S_{\text{Risk}} \times 0.2)$$

* **$S_{\text{Tech}}$ (기술적 지표 점수 - 100점 만점)**:
* RSI (30 이하: 100점, 70 이상: 0점, 50 부근: 50점 선형 보정)
* MACD Golden Cross 발생 시 +25점
* 단기 정배열(MA5 > MA20 > MA60) 시 +25점
* 거래량 전시간 대비 30% 이상 급증 시 +25점
* 매수 호가 잔량 우위(Imbalance > 15%) 시 +25점


* **$S_{\text{AI}}$ (LLM Score - 100점 만점)**: OpenAI Structured Output 점수
* **$S_{\text{Risk}}$ (리스크 점수 - 100점 만점)**: 변동성(ATR) 과열 여부에 따른 감점 처리

---

## 4. AI Structured Output 스키마 (OpenAI API 연동)

Python에서 OpenAI API 호출 시 **JSON Schema Enforcement**를 사용하여 100% 규격화된 포맷으로 반환받도록 처리합니다.

### Prompt 구조

```text
System: You are an expert quantitative crypto trader. Analyze the pre-calculated technical indicators and orderbook metrics for {market}. Do NOT fabricate raw price data. Evaluate short-term (5m, 15m) probability and return strict JSON format.

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

---

## 5. 단계별 구현 및 개발 로드맵 (AI Coding Prompt 분할)

AI 코더에게 작업을 지시할 때는 아래 **[Step 1] ~ [Step 5]** 프롬프트를 한 단계씩 순차적으로 제공해 개발을 진행합니다.

---

### [Step 1] Python Upbit WebSocket & Indicator Engine 구축

```text
[Prompt for AI Coder]

우리는 가상자산 분석 SaaS를 구축 중입니다. First Step으로 Python기반 Upbit WebSocket 수집 및 기술적 지표 계산 모듈을 작성해주세요.

[요구사항]
1. `websockets` 및 `asyncio` 라이브러리를 사용하여 Upbit WebSocket API에 접속하세요.
2. 수집 대상: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE
3. 체결(ticker) 및 호가(orderbook) 실시간 데이터를 수집하여 Redis에 즉시 Caching 하세요.
4. `pandas_ta` 또는 `ta` 라이브러리를 사용하여 최근 100개 캔들 기준 다음 지표를 실시간 계산하는 클래스를 만드세요:
   - RSI (14)
   - MACD (12, 26, 9) & Signal Cross 여부
   - Moving Averages (MA5, MA20, MA60) 정배열/역배열 판단
   - Orderbook Imbalance (매수 잔량 / 매도 잔량 비율)
5. 지표가 업데이트되면 룰 엔진 평가 함수를 호출하도록 Event Structure를 설계하세요.

```

---

### [Step 2] Rule Engine + OpenAI API 연동 및 DB 저장

```text
[Prompt for AI Coder]

Python 프로젝트에 룰 기반 평가 알고리즘과 OpenAI Structured Output API를 통합하세요.

[요구사항]
1. 기술적 지표들을 입력받아 0~100점 사이의 `Technical Score`를 산출하는 `RuleEngine` 클래스를 만드세요.
2. `Technical Score`가 70점 이상이거나 30점 이하일 때만 OpenAI API(`gpt-5.6-luna`)를 호출하도록 필터링 로직을 작성하세요. (비용 절감)
3. OpenAI API 호출 시 Pydantic/JSON Schema를 활용하여 규격화된 JSON 분석 결과(signal, score, up_prob, reasons, risks)를 받아오세요.
4. 지표 점수(60%) + AI 점수(20%) + 리스크 점수(20%)를 합성한 최종 Score를 계산하세요.
5. 계산된 최종 신호를 MySQL의 `ai_signals` 테이블에 저장하고, Redis Pub/Sub 채널 (`channel:signals`)로 Publish 하세요.

```

---

### [Step 3] 백테스팅 엔진 구현 (수수료/슬리피지 포함)

```text
[Prompt for AI Coder]

Python으로 과거 업비트 OHLCV 데이터를 기반으로 전략 성과를 측정하는 백테스팅 모듈을 개발하세요.

[요구사항]
1. Upbit REST API로 과거 1분/5분/1시간 캔들 데이터를 수집/파싱하세요.
2. 백테스트 매매 조건:
   - 매수: AI Combined Score >= 80
   - 매도: AI Combined Score <= 30 또는 익절(+1.5%), 손절(-1.0%) 조건 달성 시
3. 필수 반영 파라미터:
   - 업비트 거래 수수료: 0.05% (매수/매도 각각 적용)
   - 슬리피지(Slippage): 0.05% 적용
4. 출력 결과 지표:
   - 초기 자산 대비 최종 자산 및 총 수익률 (%)
   - 승률 (Win Rate %)
   - Total Trades, 평균 수익/손실 비율
   - MDD (Maximum Drawdown %)
5. 실행 결과를 MySQL `backtest_logs` 테이블에 저장하는 파이프라인을 작성하세요.

```

---

### [Step 4] PHP REST API & Dashboard Backend

```text
[Prompt for AI Coder]

PHP (순수 PHP 또는 Laravel/CodeIgniter 스타일)를 사용하여 웹 프론트엔드가 사용할 RESTful API를 작성해주세요.

[요구사항]
1. DB 접속 정보는 `.env` 파일에서 읽어오도록 처리하세요.
2. API Endpoints 구현:
   - `GET /api/v1/signals/latest`: 가장 최근에 발생한 신호 목록 조회 (`ai_signals` 테이블)
   - `GET /api/v1/signals/strong`: Score가 80 이상인 Strong Buy 신호 카드 데이터
   - `GET /api/v1/market/summary`: Redis에서 실시간 시세 및 AI 점수 캐시 읽어와 출력
   - `POST /api/v1/backtest/run`: 백테스트 요청 수신 후 백그라운드 Python 프로세스에 전달
3. CORS 처리, JWT 또는 Session 기반 사용자 인증 Middleware 구조를 포함하세요.

```

---

### [Step 5] 시그널 성과 자동 추적 스케줄러 (Daemon)

```text
[Prompt for AI Coder]

과거에 생성된 AI 시그널이 실제로 적중했는지 자동 평가하는 Python 크론/스케줄러 데몬을 작성하세요.

[요구사항]
1. MySQL `ai_signals` 테이블에서 생성된 지 5분, 15분, 1시간이 지난 시그널 중 `ai_signal_results`에 기록되지 않은 건을 조회하세요.
2. 시그널 발생 시점의 `entry_price` 대비 5분/15분/1시간 후의 실제 업비트 가격을 조회하세요.
3. 수익률(`return_5m`, `return_15m`, `return_1h`)을 계산하세요.
4. 판단 로직:
   - `BUY` 시그널인 경우: 5분 후 수익률 > +0.2% 이면 `is_accurate = 1`, 아니면 `0`
   - `SELL` 시그널인 경우: 5분 후 수익률 < -0.2% 이면 `is_accurate = 1`, 아니면 `0`
5. 평가 결과를 `ai_signal_results` 테이블에 INSERT/UPDATE 하세요.

```

---

## 6. 개발 시 보안 및 운용 필수 체크리스트

1. **API Key 분리 및 보안**:
* 본인 자동매매용 Upbit API Key는 PHP 웹 루트 디렉토리 외부에 있는 Python 실행 환경의 `.env`에만 보관
* Upbit API Key 설정 시 **IP 제한 (서버 고정 IP)** 설정 필수 및 **출금 권한 제외 (조회 및 매매 권한만 부여)**


2. **Rate Limit 관리**:
* Upbit WebSocket 연결 끊김 재접속(Reconnection) 및 Backoff 로직 구현
* Upbit REST API 초당 요청 수를 초과하지 않도록 Redis 기반 Throttling 적용


3. **법적 고지 (Disclaimer)**:
* 모든 Dashboard 및 Signal 화면 하단에 아래 문구 필수 노출:
> *"본 서비스에서 제공하는 분석 결과 및 AI 신호는 투자 참고용 데이터이며, 수익을 보장하지 않습니다. 모든 투자의 최종 책임은 본인에게 있습니다."*





이 명세서를 AI 코딩 도구에 단계별 프롬프트로 제시하면서 순차적으로 구축을 진행하실 수 있습니다.

---

### 다음 단계 추천 제안