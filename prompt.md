# [PRD & Dev Spec] AI Trading Platform — v2 (다중 거래소 전환)

본 문서는 **다중 거래소(ccxt) 기반 Python 트레이딩/AI 분석 엔진**과 **PHP/MySQL 기반 SaaS 웹 서비스**로 구성된 실시간 가상자산 분석 및 신호 제공 플랫폼의 상세 기술 명세서입니다. AI 코딩 에이전트(Cursor, Claude Code, GitHub Copilot 등)에 아래 [Step] 프롬프트를 순차적으로 투입해 구현합니다.

> **v1 대비 변경 요약**
> - 시세/지표 데이터 소스: 업비트 단일 → **`ccxt` 기반 다중 거래소(Binance/OKX/Bybit/Coinbase/Upbit)**
> - 지표 확장: RSI/MACD/MA/거래량/호가불균형 → **+ Bollinger Bands, Stochastic, ADX, CCI**
> - 신규 개념: **거래소 간 합의(Exchange Consensus)** — 여러 거래소가 동일 방향 신호를 보이는 비율
> - **트레이딩뷰는 공개 데이터 API가 없으므로 시세/지표 소스로 쓰지 않는다.** 대신 **유저 본인이 트레이딩뷰 유료 플랜(Essential 이상)을 갖고 있으면, 유저별 고유 웹훅 URL을 발급해 본인 전략의 알림을 개인 대시보드로 중계받을 수 있는 멀티테넌트 "Bring Your Own TradingView" PRO 기능**으로 스코프를 정의한다. 비공식 스크래핑 라이브러리(tradingview-screener 등)는 이용약관 리스크로 사용 금지.
> - 자동매매 실행은 **여전히 업비트 전용, 본인 계정만.** 고객에게는 어떤 거래소의 API Key도 수집하지 않는다.
> - **다국어(i18n) 지원 추가**: 한국어(ko, 기본값)/영어(en)/일본어(ja)를 우선 지원하되, 코드 수정 없이 언어를 추가할 수 있는 구조(설정 기반 언어 목록 + 언어별 번역 파일)로 설계한다.

---

## 1. 시스템 아키텍처 및 역할 분담

```
[ Binance / OKX / Bybit / Coinbase / Upbit (REST / WebSocket, ccxt) ]
               │
               ▼
     ┌──────────────────────┐
     │  Python Engine       │ (Daemon)
     │  - Multi-Exchange    │
     │    Collector         │
     │  - Indicator Engine  │
     │  - Consensus Engine  │
     └────────┬──────────────┘
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
     │  PHP API Server   │ (Web SaaS)
     └────────┬─────────┘
              │ HTTPS / REST API
     ┌────────┴─────────┐
     │ Client (Web/Mobile)│
     └───────────────────┘

     ┌────────────────────────────┐
     │ (선택) TradingView Webhook │──▶ Python Engine의 external/ 모듈로 유입,
     │  Receiver (Pine Alert)     │   ai_signals의 보조 입력으로만 반영
     └────────────────────────────┘

     [ PRIVATE ONLY — 위 다중 거래소 수집과 자격증명·코드 경로 완전 분리 ]
     Upbit Private Account ── 본인 전용 자동매매 주문 실행
```

- **Python Engine**: `ccxt`로 다중 거래소 WebSocket/REST 수집, 거래소별 기술적 지표 계산, 거래소 간 합의(Consensus) 산출, Rule Engine 평가, OpenAI API 연동, (선택) TradingView 웹훅 수신, 가상/실제 매매 실행(Upbit 전용), Redis 캐싱 및 MySQL 영구 저장
- **Redis**: 거래소별 Real-time Ticks/Orderbook, In-Memory Indicators, 거래소 간 합의 캐시, Pub/Sub 이벤트 버스
- **MySQL**: 사용자, 구독, 거래소 메타(`exchanges`), AI 분석 신호(`ai_signals`), 신호 결과 검증(`ai_signal_results`), 보조 신호 원본(`external_signals`), 백테스트 이력
- **PHP Web/API**: 회원가입/인증, PG 결제 연동, AI Signal Dashboard REST API, 백테스트 실행 요청 전달

---

## 2. DB 스키마 (MySQL DDL)

AI 코더가 그대로 실행하여 테이블을 생성할 수 있는 완전한 DDL입니다. (v1의 5개 테이블에 `exchanges`, `external_signals`를 추가하고 `ai_signals`에 컬럼을 추가했습니다.)

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
    `locale` VARCHAR(10) NOT NULL DEFAULT 'ko', -- 'ko' | 'en' | 'ja' 등. supported_locales 설정에 등록된 값만 허용 (앱 레벨 검증)
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

-- 3. 거래소 메타 테이블 (신규)
CREATE TABLE `exchanges` (
    `id` SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(20) NOT NULL UNIQUE, -- ccxt id: 'binance', 'okx', 'bybit', 'coinbase', 'upbit'
    `display_name` VARCHAR(50) NOT NULL,
    `supports_orderbook` TINYINT(1) DEFAULT 1,
    `is_private_trading_target` TINYINT(1) DEFAULT 0, -- 본인 자동매매 실행처인지 (upbit만 1)
    `rate_limit_ms` INT UNSIGNED NOT NULL DEFAULT 1000, -- ccxt rateLimit 값
    `is_active` TINYINT(1) DEFAULT 1,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 4. AI 시그널 테이블 (핵심, v1 대비 컬럼 추가)
CREATE TABLE `ai_signals` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL, -- 예: BTC, ETH (거래소 무관 정규화된 심볼)
    `timeframe` VARCHAR(10) NOT NULL, -- 5m, 15m, 1h
    `signal_type` ENUM('STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL') NOT NULL,
    `score` TINYINT UNSIGNED NOT NULL, -- 0~100
    `up_prob` DECIMAL(5, 2) NOT NULL,
    `sideways_prob` DECIMAL(5, 2) NOT NULL,
    `down_prob` DECIMAL(5, 2) NOT NULL,
    `entry_price_global` DECIMAL(18, 8) NOT NULL, -- 거래량 가중 평균가 (글로벌)
    `entry_price_upbit` DECIMAL(18, 8) NULL, -- 업비트 KRW 가격 (참고용, 없을 수 있음)
    `rsi_val` DECIMAL(6, 2) NULL,
    `macd_val` DECIMAL(12, 4) NULL,
    `bollinger_position` ENUM('BELOW_LOWER','LOWER_HALF','UPPER_HALF','ABOVE_UPPER') NULL,
    `stochastic_k` DECIMAL(6, 2) NULL,
    `stochastic_d` DECIMAL(6, 2) NULL,
    `adx_val` DECIMAL(6, 2) NULL,
    `cci_val` DECIMAL(8, 2) NULL,
    `volume_change_pct` DECIMAL(8, 2) NULL,
    `exchange_consensus_pct` DECIMAL(5, 2) NOT NULL, -- 거래소 간 동일 방향 신호 비율
    `data_sources_json` JSON NOT NULL, -- 이 신호 계산에 사용된 거래소 목록 및 개별 스코어
    `reasons_json` JSON NOT NULL,
    `risks_json` JSON NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_symbol_created` (`symbol`, `created_at`),
    INDEX `idx_score` (`score`),
    INDEX `idx_consensus` (`exchange_consensus_pct`)
) ENGINE=InnoDB;

-- 5. AI 시그널 성과 추적 테이블
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
    `is_accurate` TINYINT(1) DEFAULT NULL,
    `evaluated_at` TIMESTAMP NULL,
    FOREIGN KEY (`signal_id`) REFERENCES `ai_signals`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 6. 유저별 TradingView 웹훅 연동 설정 (신규, 멀티테넌트 — 유저 본인 트레이딩뷰 유료 계정 전제)
CREATE TABLE `user_webhooks` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `webhook_token` CHAR(43) NOT NULL UNIQUE, -- URL에 노출되는 고유 토큰 (예: secrets.token_urlsafe(32))
    `label` VARCHAR(100) NULL, -- 유저가 붙이는 이름 (예: "BTC 볼린저 전략")
    `is_active` TINYINT(1) DEFAULT 1,
    `last_received_at` TIMESTAMP NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `revoked_at` TIMESTAMP NULL, -- 재발급/폐기 시각
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    INDEX `idx_token` (`webhook_token`)
) ENGINE=InnoDB;

-- 7. 보조 신호(트레이딩뷰 웹훅 등) 원본 로그 (신규, 유저별 연결)
CREATE TABLE `external_signals` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL, -- 이 신호가 어느 유저의 웹훅으로 들어왔는지
    `user_webhook_id` BIGINT UNSIGNED NOT NULL,
    `source` ENUM('TRADINGVIEW_WEBHOOK') NOT NULL DEFAULT 'TRADINGVIEW_WEBHOOK',
    `symbol` VARCHAR(20) NOT NULL,
    `action` ENUM('BUY','SELL','EXIT') NOT NULL,
    `strategy_name` VARCHAR(100) NULL,
    `raw_payload_json` JSON NOT NULL,
    `linked_signal_id` BIGINT UNSIGNED NULL, -- 참고로 연결한 ai_signals.id (있는 경우, 점수 소급 변경 없음)
    `received_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`user_webhook_id`) REFERENCES `user_webhooks`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`linked_signal_id`) REFERENCES `ai_signals`(`id`) ON DELETE SET NULL,
    INDEX `idx_user_symbol` (`user_id`, `symbol`, `received_at`)
) ENGINE=InnoDB;

-- 8. 백테스트 이력 테이블
CREATE TABLE `backtest_logs` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `symbol` VARCHAR(20) NOT NULL,
    `reference_exchange` VARCHAR(20) NOT NULL, -- 'GLOBAL_CONSENSUS' 또는 특정 거래소 code (실전은 'upbit' 고정)
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
```

---

## 3. 핵심 자료구조 및 알고리즘 명세

### 3.1 Python 데이터 파이프라인 자료구조

- **거래소별 WebSocket Ticks Buffer**: `collections.deque(maxlen=200)`, 거래소 코드별로 별도 버퍼 유지
- **ccxt 사용 원칙**: `ccxt.pro`(WebSocket 지원 버전)를 우선 사용하고, WebSocket 미지원 거래소는 REST 폴링(거래소별 `rateLimit` 값을 반드시 준수)으로 폴백
- **Redis Key Structure**:
  - `exchange:{code}:{symbol}:ticker` -> String (JSON: 현재가, 체결량, 변동률)
  - `exchange:{code}:{symbol}:orderbook` -> String (JSON: 매수/매도 호가 잔량 비율, 호가 미제공 거래소는 생략)
  - `exchange:{code}:{symbol}:candles:{tf}` -> List (최근 100개 캔들)
  - `consensus:{symbol}:{tf}` -> String (JSON: 거래소 간 합의 계산 결과, TTL 적용)
  - `global:{symbol}:price` -> String (거래량 가중 평균가)

### 3.2 거래소 간 합의(Exchange Consensus) 계산

```
1. 각 거래소에서 심볼별 Technical Score(§3.3) 산출
2. 방향 분류: Score >= 60 → BUY 진영, Score <= 40 → SELL 진영, 그 외 → NEUTRAL
3. Consensus % = (다수 진영에 속한 거래소 수 / 데이터가 유효한 전체 거래소 수) × 100
4. 유효 거래소가 3개 미만이면 Consensus를 계산하지 않고 신호를 HOLD로 강등 (표본 부족)
5. Consensus < 50%인 심볼은 AI 호출 대상에서 제외 (거래소 간 의견이 갈리는 상태는 신뢰도 낮음)
```

### 3.3 복합 스코어링 알고리즘 (Hybrid Score, v2)

$$\text{Final Score} = (S_{\text{Tech}} \times 0.45) + (S_{\text{Consensus}} \times 0.2) + (S_{\text{AI}} \times 0.2) + (S_{\text{Risk}} \times 0.15)$$

- **$S_{\text{Tech}}$ (기술적 지표 점수 — 100점 만점, 거래량 가중 평균 지표 기준)**:
  - **기준점 40점** (2026-09-03 개정) — 아래 가감점은 가점 +110 / 감점 -30 으로 상승 쪽에
    치우쳐 있어, 기준점 없이는 아무 일도 없는 시장의 점수가 50이 아니라 10이 된다.
    그러면 §3.2의 방향 임계값(60 이상 BUY / 40 이하 SELL)과 어긋나 조용한 장이 전부
    SELL 진영으로 몰린다. 기준점 40 + RSI 중립 10 = 50 으로 눈금의 중심을 맞춘다.
    가감점 배점과 clamp 규칙은 아래 그대로다.
  - RSI (30 이하: 100점, 70 이상: 0점, 50 부근: 50점 선형 보정) — 가중치 20점
  - MACD Golden Cross 발생 시 +15점
  - 단기 정배열(MA5 > MA20 > MA60) 시 +15점
  - 볼린저밴드: 하단 이탈 후 복귀 시 +10점, 상단 돌파 지속 시 -10점
  - 스토캐스틱: %K가 20 이하에서 %D 상향 돌파 시 +10점, 80 이상 과매수 시 -10점
  - ADX 25 이상(추세 강함) 시 방향에 맞춰 +10점
  - CCI: -100 이하에서 반등 시 +10점
  - 거래량 전 시간 대비 30% 이상 급증 시 +10점
  - 매수 호가 잔량 우위(Imbalance > 15%, 호가 제공 거래소 평균) 시 +10점
  - 합산 후 `min(sum, 100)`으로 clamp
- **$S_{\text{Consensus}}$**: §3.2에서 계산한 Exchange Consensus %를 그대로 사용 (0~100)
- **$S_{\text{AI}}$**: OpenAI Structured Output 점수 (§4)
- **$S_{\text{Risk}}$**: 변동성(ATR) 과열 여부 및 Consensus 표본 수 부족 시 감점

---

## 4. AI Structured Output 스키마 (OpenAI API 연동)

### Prompt 구조

```
System: You are an expert quantitative crypto trader. Analyze the pre-calculated, multi-exchange-aggregated technical indicators for {symbol}. This data is a volume-weighted composite across multiple exchanges (see data_sources), not from a single exchange. Do NOT fabricate raw price data. Evaluate short-term (5m, 15m) probability and return strict JSON format.

User Data:
{
  "symbol": "BTC",
  "global_price_usd": 61420.5,
  "upbit_price_krw": 84300000,
  "rsi_14": 52.31,
  "macd_status": "GOLDEN_CROSS",
  "bollinger_position": "UPPER_HALF",
  "stochastic_k": 62.1,
  "stochastic_d": 58.4,
  "adx": 28.3,
  "cci": 140.2,
  "volume_surge_pct": 37.2,
  "orderbook_imbalance": 21.4,
  "ma_trend": "BULLISH",
  "exchange_consensus_pct": 82.0,
  "data_sources": ["binance", "okx", "bybit", "coinbase", "upbit"]
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

## 5. 트레이딩뷰 연동 방식 — 명확한 스코프 정의 (멀티테넌트 플랫폼 역할)

**트레이딩뷰는 시세/지표 조회용 공개 데이터 API를 제공하지 않는다.** 따라서 아래 스코프를 벗어나는 트레이딩뷰 연동은 이 프로젝트에서 구현하지 않는다.

### 사용하는 것: 유저별 웹훅 알림 수신 — "Bring Your Own TradingView" (PRO 기능, §6 Step 3)

우리는 트레이딩뷰 데이터를 우리 서버가 대신 가져오는 것이 아니라, **이미 트레이딩뷰 유료 플랜(Essential 이상)을 보유한 유저가 자기 전략의 알림을 우리 대시보드로 중계받을 수 있게 해주는 플랫폼 역할**만 한다.

```
1. 유저가 대시보드에서 "TradingView 연동" 메뉴 진입
2. 서버가 유저 전용 웹훅 URL 발급: https://api.example.com/webhook/tv/{user_webhook_token}
3. 유저가 본인 트레이딩뷰 차트에서 직접 만든 Pine Script 전략/지표에 Alert를 걸고,
   Webhook URL 필드에 위 URL을 등록 (이 단계는 전부 유저의 트레이딩뷰 계정 안에서 일어남)
4. 유저가 설계한 알림 조건이 맞으면 트레이딩뷰가 그 URL로 JSON을 POST
5. 우리 서버는 URL에 포함된 token으로 어느 유저의 신호인지 식별하여
   `external_signals`에 저장 → 해당 유저의 대시보드에만 표시
```

- 이 신호는 다른 유저와 절대 섞이지 않는다 (`user_webhooks.webhook_token`으로 격리)
- 해당 시각에 산출 중이던 우리 자체 `ai_signals`가 있으면 참고용으로 `linked_signal_id`만 연결하고, **점수를 소급 변경하지 않는다**
- **이 신호를 신뢰할 수 있는 "시세 데이터"로 취급하지 않는다.** 어디까지나 "유저 본인이 설계한 조건 하나가 맞았다"는 이벤트일 뿐이며, UI에도 그렇게 표기한다
- 유저는 언제든 토큰을 재발급(폐기 후 재발급)할 수 있어야 한다 (`user_webhooks.revoked_at`)
- 이 기능은 트레이딩뷰 유료 플랜이 없는 유저에게는 의미가 없으므로, **PRO 이상 부가 기능**으로 노출하고 무료/기본 상품(다중 거래소 AI 신호 엔진)과는 완전히 독립적으로 동작해야 한다

### 사용하지 않는 것

- `tradingview-screener`, `tradingview_ta` 등 비공식 스크래핑 라이브러리 — 트레이딩뷰 비공식 내부 엔드포인트를 호출하는 방식이라 이용약관 위반/무통보 차단 리스크가 있음. **이 프로젝트에서는 사용하지 않는다.**
- "트레이딩뷰 시세를 가져온다"는 표현 — 사실이 아니므로 코드 주석, DB 컬럼명, 마케팅 문구 어디에도 쓰지 않는다
- 유저 간 웹훅 신호 공유 — 한 유저의 트레이딩뷰 알림을 다른 유저에게 노출하지 않는다 (개인 전략 정보이므로)

---

## 6. 단계별 구현 및 개발 로드맵 (AI Coding Prompt 분할)

AI 코더에게 작업을 지시할 때는 아래 **[Step 1] ~ [Step 7]** 프롬프트를 한 단계씩 순차적으로 제공해 개발을 진행합니다.

---

### [Step 1] Multi-Exchange Collector & Indicator Engine 구축 (ccxt 기반)

```
[Prompt for AI Coder]

우리는 가상자산 분석 SaaS를 구축 중입니다. First Step으로 Python 기반 다중 거래소 시세 수집 및 기술적 지표 계산 모듈을 작성해주세요.

[요구사항]
1. `ccxt`(가능하면 `ccxt.pro`) 라이브러리를 사용하여 아래 거래소에 접속하세요: Binance, OKX, Bybit, Coinbase, Upbit
2. 수집 심볼(공통 상장 기준): BTC, ETH, XRP, SOL, DOGE
3. 거래소별로 체결(ticker) 및 호가(orderbook, 지원하는 거래소만) 실시간 데이터를 수집하여 Redis에 `exchange:{code}:{symbol}:*` 키로 즉시 캐싱하세요.
4. 거래소별 WebSocket 연결이 끊기면 지수 백오프로 재접속하세요. 한 거래소 장애가 다른 거래소 수집에 영향을 주지 않도록 거래소별로 독립된 태스크/커넥션으로 구성하세요.
5. `pandas_ta` 또는 `ta` 라이브러리를 사용하여 거래소별 최근 100개 캔들 기준 다음 지표를 계산하는 클래스를 만드세요:
   - RSI (14)
   - MACD (12, 26, 9) & Signal Cross 여부
   - Moving Averages (MA5, MA20, MA60) 정배열/역배열 판단
   - Bollinger Bands (20, 2) 및 현재가 위치
   - Stochastic (%K 14, %D 3)
   - ADX (14)
   - CCI (20)
   - Orderbook Imbalance (호가 제공 거래소만)
6. 심볼별로 거래소 간 거래량 가중 평균가/지표를 계산하는 `market_manager.py`를 작성하고, 결과를 `global:{symbol}:price` 등 Redis 키로 캐싱하세요.
7. 지표가 업데이트되면 룰 엔진 평가 함수를 호출하도록 Event Structure를 설계하세요.
8. Upbit 관련 코드는 이 수집기 모듈(`market/`) 안에서는 "여러 거래소 중 하나"로만 취급하고, 자동매매용 Private API 클라이언트(§Step 5)와는 파일/클래스를 완전히 분리하세요.
```

---

### [Step 2] 거래소 간 합의(Consensus) + Rule Engine + OpenAI API 연동 및 DB 저장

```
[Prompt for AI Coder]

Python 프로젝트에 거래소 간 합의 계산, 룰 기반 평가 알고리즘, OpenAI Structured Output API를 통합하세요.

[요구사항]
1. Step 1에서 계산된 거래소별 지표를 입력받아, prompt.md §3.2의 로직대로 `Exchange Consensus %`를 계산하는 함수를 작성하세요. 유효 거래소가 3개 미만이면 신호를 HOLD로 강등하세요.
2. 거래량 가중 평균 지표를 입력받아 0~100점 사이의 `Technical Score`를 산출하는 `RuleEngine` 클래스를 prompt.md §3.3 배점표대로 만드세요. `min(sum, 100)`으로 clamp하세요.
3. `Technical Score`가 70점 이상이거나 30점 이하이면서, `Exchange Consensus`가 50% 이상일 때만 OpenAI API(`gpt-5.6-luna`)를 호출하도록 필터링 로직을 작성하세요. (비용 절감 + 신뢰도 낮은 신호 사전 차단)
4. OpenAI API 호출 시 Pydantic/JSON Schema(prompt.md §4)를 활용하여 규격화된 JSON 분석 결과를 받아오세요.
5. prompt.md §3.3의 가중식(Tech 45% + Consensus 20% + AI 20% + Risk 15%)으로 최종 Score를 계산하세요.
6. 계산된 최종 신호를 MySQL의 `ai_signals` 테이블에 저장하세요. `data_sources_json`에는 이 신호 계산에 사용된 거래소 목록과 거래소별 개별 스코어를 기록하세요. Redis Pub/Sub 채널(`channel:signals`)로도 Publish 하세요.
```

---

### [Step 3] 유저별 TradingView 웹훅 연동 — 멀티테넌트 (PRO 기능)

```
[Prompt for AI Coder]

이미 트레이딩뷰 유료 플랜을 보유한 유저가 자기 전략의 알림(Pine Script Alert)을 우리 대시보드로 개인화하여 받을 수 있는 멀티테넌트 웹훅 기능을 작성하세요. 이것은 트레이딩뷰의 시세 데이터를 우리가 가져오는 기능이 아니라, 유저가 직접 만든 알림 이벤트를 유저별로 격리해서 수신하는 기능입니다.

[요구사항]
1. PHP API에 `POST /api/v1/webhooks/tradingview` (유저 인증 필요, JWT)를 작성해 유저가 새 웹훅 연동을 발급받을 수 있게 하세요.
   - `secrets` 모듈 등으로 URL-safe 랜덤 토큰(32바이트 이상)을 생성해 `user_webhooks` 테이블에 저장하세요.
   - 응답으로 완성된 웹훅 URL(`https://.../webhook/tv/{token}`)을 반환하세요.
   - 유저가 토큰을 재발급(폐기 후 재발급)할 수 있는 `DELETE /api/v1/webhooks/tradingview/{id}` + 재발급 엔드포인트도 만드세요.
2. Python(FastAPI 등)으로 실제 수신 엔드포인트 `POST /webhook/tv/{token}`을 작성하세요. (PHP가 아닌 Python 엔진 쪽에서 수신 — 다른 신호 파이프라인과 동일한 프로세스에서 처리하기 위함)
3. `{token}`으로 `user_webhooks` 테이블을 조회하여 `is_active=1`이고 `revoked_at IS NULL`인 경우에만 처리하세요. 매칭 실패 시 404를 반환하되, 존재 여부를 추측할 수 있는 힌트를 응답에 넣지 마세요.
4. 요청 바디는 JSON이며 최소 필드는 `symbol`, `action`(BUY/SELL/EXIT)만 강제하고, `strategy_name`/`price`/`time` 등 나머지는 유저마다 Pine Script가 다르므로 있으면 저장, 없으면 null로 유연하게 처리하세요.
5. 수신한 원본 payload를 `external_signals`에 `user_id`, `user_webhook_id`와 함께 저장하고 `user_webhooks.last_received_at`을 갱신하세요.
6. 수신 시각·심볼 기준으로 최근 생성된 `ai_signals`가 있으면 `linked_signal_id`로 참고 연결만 하고, **AI 신호의 점수/등급을 소급 변경하지 마세요.**
7. 토큰별 Rate limiting을 적용해 과도한 요청을 차단하세요.
8. 유저 A의 웹훅으로 들어온 신호가 유저 B의 대시보드에 절대 노출되지 않는지 테스트 케이스로 검증하세요.
9. 이 기능 전체가 비활성화되거나 특정 유저가 미사용이어도 나머지 시스템(다중 거래소 신호 생성)이 정상 동작해야 합니다.
```

---

### [Step 4] 백테스팅 엔진 구현 (신호 검증용 / 업비트 실전용 분리)

```
[Prompt for AI Coder]

Python으로 두 종류의 백테스트를 지원하는 모듈을 개발하세요: (a) 다중 거래소 합의 데이터 기준 신호 검증용, (b) 업비트 실전 자동매매 기준용.

[요구사항]
1. 각 거래소(ccxt REST) 및 업비트 REST API로 과거 1분/5분/1시간 캔들 데이터를 수집/파싱하세요.
2. `backtest_logs.reference_exchange` 값이 'GLOBAL_CONSENSUS'면 다중 거래소 합의 데이터 기준으로, 특정 거래소 code(예: 'upbit')면 해당 거래소 단독 데이터 기준으로 백테스트하세요.
3. 백테스트 매매 조건:
   - 매수: Final Score >= 80
   - 매도: Final Score <= 30 또는 익절(+1.5%), 손절(-1.0%) 조건 달성 시
4. 업비트 실전용(reference_exchange='upbit') 백테스트에는 반드시 업비트 실제 수수료(0.05%, 매수/매도 각각) 및 슬리피지(0.05%)를 반영하세요. 다중 거래소 신호 검증용은 참고용 평균 수수료를 사용하되, 두 결과를 절대 혼합해서 하나의 숫자로 보여주지 마세요.
5. 출력 결과 지표: 총 수익률(%), 승률(%), Total Trades, 평균 수익/손실 비율, MDD(%)
6. 실행 결과를 MySQL `backtest_logs` 테이블에 저장하는 파이프라인을 작성하세요.
```

---

### [Step 5] 실거래 안전장치 + 매매 실행 (Upbit 전용, 본인 계정만)

```
[Prompt for AI Coder]

Python으로 업비트 전용 자동매매 실행 모듈을 작성하세요. 이 모듈은 위의 다중 거래소 수집/분석 모듈과 파일·클래스 레벨에서 명확히 분리되어야 합니다.

[요구사항]
1. `trading/` 패키지 하위에 업비트 Private API(주문/잔고 조회) 클라이언트를 작성하세요. API Key/Secret은 `.env`에서만 읽고, 어떤 로그에도 원문을 남기지 마세요.
2. 기본 모드는 PAPER(가상매매)로 하고, LIVE 전환은 명시적 설정 변경 + 감사 로그 기록이 있어야만 가능하게 하세요.
3. 기동 시 Upbit 키에 출금 권한이 없는지 검증하는 체크를 넣으세요. 출금 권한이 확인되면 즉시 실행을 중단하세요.
4. 일일 손실 한도를 초과하면 `kill_switch_active=1`로 신규 주문을 자동 차단하고, 수동 해제 전까지 유지하세요.
5. 단일 주문이 `max_position_size_krw` 설정값을 넘지 않도록 검증하세요.
6. 매매 신호 입력은 §Step 2에서 생성한 `ai_signals` 중 `symbol`이 업비트 상장 종목인 것만 사용하세요. 다중 거래소 합의는 참고하되, 실제 체결 가격/잔고 조회는 반드시 업비트 API로만 수행하세요.
```

---

### [Step 6] PHP REST API & Dashboard Backend

```
[Prompt for AI Coder]

PHP(순수 PHP 또는 Laravel/CodeIgniter 스타일)를 사용하여 웹 프론트엔드가 사용할 RESTful API를 작성해주세요.

[요구사항]
1. DB 접속 정보는 `.env` 파일에서 읽어오도록 처리하세요.
2. API Endpoints 구현:
   - `GET /api/v1/signals/latest`: 최근 신호 목록 조회, `exchange_consensus_pct`와 `data_sources_json`을 응답에 포함
   - `GET /api/v1/signals/strong`: Score 80 이상 신호 카드 데이터
   - `GET /api/v1/market/summary`: Redis에서 실시간 글로벌 시세(`global:{symbol}:price`) 및 AI 점수 캐시 읽어와 출력
   - `POST /api/v1/backtest/run`: `reference_exchange` 파라미터(GLOBAL_CONSENSUS 또는 거래소 code)를 받아 백테스트 요청을 Python 프로세스에 전달
3. CORS 처리, JWT 또는 Session 기반 사용자 인증 Middleware 구조를 포함하세요.
4. 모든 신호/대시보드 응답에는 §8의 법적 고지 문구를 프론트에서 표시할 수 있도록 별도 필드나 상수로 제공하세요.
```

---

### [Step 7] 시그널 성과 자동 추적 스케줄러 (Daemon)

```
[Prompt for AI Coder]

과거에 생성된 AI 시그널이 실제로 적중했는지 자동 평가하는 Python 크론/스케줄러 데몬을 작성하세요.

[요구사항]
1. MySQL `ai_signals` 테이블에서 생성된 지 5분, 15분, 1시간이 지난 시그널 중 `ai_signal_results`에 기록되지 않은 건을 조회하세요.
2. 신호의 `entry_price_global`(다중 거래소 가중 평균 기준가) 대비 5분/15분/1시간 후의 실제 글로벌 가중 평균가를 조회하세요.
3. 수익률(`return_5m`, `return_15m`, `return_1h`)을 계산하세요.
4. 판단 로직:
   - `BUY` 계열 시그널: 5분 후 수익률 > +0.2% 이면 `is_accurate = 1`, 아니면 `0`
   - `SELL` 계열 시그널: 5분 후 수익률 < -0.2% 이면 `is_accurate = 1`, 아니면 `0`
   - `HOLD`는 평가 대상에서 제외
5. 평가 결과를 `ai_signal_results` 테이블에 INSERT/UPDATE 하세요. Redis 분산 락으로 중복 실행을 방지하세요.
```

---

### [Step 8] 다국어 지원 (i18n) — 한국어/영어/일본어, 확장 가능한 구조

```
[Prompt for AI Coder]

PHP 웹서비스와 프론트엔드에 다국어(i18n) 지원을 추가하세요. 초기 지원 언어는 한국어(ko, 기본값)/영어(en)/일본어(ja)이지만, 코드 수정 없이 언어를 추가할 수 있는 구조여야 합니다.

[요구사항]
1. 지원 언어 목록을 하드코딩하지 말고 설정 파일(예: `config/locales.php` 또는 `.env`의 `SUPPORTED_LOCALES=ko,en,ja`)로 관리하세요. 새 언어 추가는 번역 파일 1개 + 이 설정에 코드 추가만으로 끝나야 합니다.
2. UI 정적 텍스트는 언어별 번역 파일로 분리하세요: `lang/ko.json`, `lang/en.json`, `lang/ja.json`. 키 구조는 두 파일 모두 동일해야 하며, 특정 언어 파일에 키가 누락되면 기본 언어(ko)로 폴백하는 로직을 넣으세요.
3. `users.locale` 컬럼에 유저의 언어 설정을 저장하세요. 회원가입 시 브라우저의 `Accept-Language` 헤더로 기본값을 추정하되, 유저가 마이페이지에서 언제든 변경할 수 있게 하세요.
4. PHP API 응답의 에러 메시지, 알림(이메일/웹푸시) 문구도 유저의 `locale`을 따르도록 하세요.
5. AI가 생성하는 신호의 `reasons`/`risks` 문구는 실시간 생성 텍스트라 정적 번역이 불가능합니다. 다음 방식으로 처리하세요:
   - `ai_signals`에는 항상 한 가지 기준 언어(예: 한국어)로만 저장한다.
   - 화면에 표시할 때 유저의 `locale`이 기준 언어와 다르면, 짧은 문구 배열(`reasons`, `risks`)만 별도 번역 API(OpenAI 등)로 즉석 번역하고, 동일 문구는 캐싱(Redis, TTL 적용)하여 반복 번역 비용을 줄이세요.
   - 번역 결과에는 "AI 자동 번역"임을 명시하는 UI 배지를 붙이세요.
6. 숫자(점수, 퍼센트, 합의율)는 번역하지 않되, 통화·날짜 형식은 유저의 `locale`에 맞춰 포맷팅하세요 (예: ko는 '원' 표기, en/ja는 통화 기호 또는 자국 표기 규칙).
7. 법적 고지(§7 Disclaimer) 문구는 기계번역이 아니라 언어별로 검수된 고정 문구를 `lang/*.json`에 직접 넣으세요 (법적 문구는 오역 리스크가 크므로 실시간 번역 대상에서 제외).
```

---

## 7. 개발 시 보안 및 운용 필수 체크리스트

1. **API Key 분리 및 보안**
   - 본인 자동매매용 Upbit API Key는 PHP 웹 루트 디렉토리 외부의 Python 실행 환경 `.env`에만 보관
   - Upbit API Key는 **IP 제한(서버 고정 IP)** 설정 필수, **출금 권한 제외**(조회·매매 권한만)
   - 다중 거래소 수집용 API Key(있는 경우, 대부분 공개 시세는 키 없이 조회 가능)도 동일 원칙 적용
   - TradingView 웹훅 `secret`은 별도 값으로 관리하고 주기적으로 재발급

2. **Rate Limit 관리**
   - 거래소별 `ccxt` `rateLimit` 값을 반드시 준수하고, Redis 기반 Throttling을 거래소별로 개별 적용
   - 한 거래소의 API 정책 변경/차단이 다른 거래소 수집에 영향을 주지 않도록 격리
   - Upbit WebSocket 연결 끊김 재접속(Reconnection) 및 Backoff 로직 구현

3. **법적 고지 (Disclaimer)**
   - 모든 Dashboard 및 Signal 화면 하단에 아래 문구 필수 노출:
   > *"본 서비스에서 제공하는 분석 결과 및 AI 신호는 투자 참고용 데이터이며, 수익을 보장하지 않습니다. 모든 투자의 최종 책임은 본인에게 있습니다. 표시되는 시세는 여러 거래소의 데이터를 자체 집계한 것이며, 트레이딩뷰(TradingView)의 공식 데이터가 아닙니다."*

4. **명칭/마케팅 정확성**
   - "트레이딩뷰 API로 시세를 받아온다"는 표현을 코드 주석, DB 컬럼명, README, 마케팅 문구 어디에도 사용하지 않는다 (사실과 다름 — §5 참고)
   - 비공식 트레이딩뷰 스크래핑 라이브러리(`tradingview-screener`, `tradingview_ta` 등)를 의존성에 추가하지 않는다

5. **유저별 웹훅 토큰 격리 (멀티테넌트)**
   - `user_webhooks.webhook_token`은 예측 불가능한 값(32바이트 이상 랜덤)이어야 하며, 데이터베이스 조회 시 상수시간 비교를 사용하는 것을 권장한다
   - 한 유저의 웹훅 신호가 다른 유저에게 노출되지 않는지 자동화 테스트로 반드시 검증한다
   - 토큰 재발급 시 이전 토큰은 즉시 무효화(`revoked_at` 기록)한다

6. **다국어(i18n) 관련 주의사항**
   - 법적 고지 문구는 언어별로 검수된 고정 문구를 사용하고, 실시간 기계번역 대상에서 제외한다
   - AI 생성 문구(`reasons`/`risks`)를 번역해 보여줄 때는 반드시 "AI 자동 번역" 배지를 표시해 원문과 구분한다

이 명세서를 AI 코딩 도구에 단계별 프롬프트로 제시하면서 순차적으로 구축을 진행하실 수 있습니다.