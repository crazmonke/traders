# 외부 데이터 아카이브 설계 — 뉴스 · 매크로 · 커뮤니티

2026-09-02 논의. `prompt.md` v2 범위 밖의 확장 제안이며, 구현 전 설계 합의용 문서다.

## 왜 지금 시작하나

**과거 데이터는 소급 확보가 안 된다.** RSS는 최신 수십 건만 주고, 매크로 지표도
"그때 발표된 값"(개정 전 원본)은 지금 모아두지 않으면 나중에 복원하기 어렵다.
신호에 반영할지 말지는 나중에 정해도 되지만, **모으는 것만은 지금 시작해야 한다.**

## 네 가지 원칙

1. **점입시점 정확성(point-in-time)** — 모든 레코드에 "사건이 일어난 시각"과
   "우리가 그것을 본 시각"을 따로 남긴다. 이게 없으면 백테스트가 환상이 된다.
   8월 CPI가 9월 10일에 발표됐다면, 9월 9일 백테스트는 그 값을 몰라야 한다.
2. **수집과 반영을 분리한다** — 아카이브가 쌓이고 상관관계가 검증되기 전까지
   신호 점수에 넣지 않는다. 근거 없이 가중치에 넣으면 잘 도는 Consensus 신호를 오염시킨다.
3. **공식 API·RSS만 쓴다** — 비공식 스크래핑은 하지 않는다.
   `prompt.md` §5에서 트레이딩뷰 스크래핑을 거부한 것과 같은 원칙이다.
4. **원문을 재배포하지 않는다** — 제목·요약·원문 링크까지만 저장하고 노출한다.
   본문 전재는 저작권 문제다.

## 데이터를 셋으로 나눈다

한 테이블에 뭉치면 셋 다 어색해진다. 성질이 다르다.

| 계층 | 성질 | 예 |
| --- | --- | --- |
| **뉴스** | 텍스트 이벤트. 값이 없고 개정되지 않는다 | "연준 위원 매파 발언", "이란 긴장 고조" |
| **매크로 시계열** | 수치. 주기적으로 갱신되고 **개정된다** | 연방기금금리, CPI, 10년물, WTI, 금, DXY |
| **경제 캘린더** | 미래 시점을 가리키는 예정 이벤트 | "FOMC 9/17 03:00", "CPI 발표 9/11" |

캘린더를 따로 두는 이유: "2시간 뒤 FOMC"는 뉴스도 지표도 아니지만
**그 자체로 거래 가능한 상태**다. 이벤트 전 변동성 축소는 실재하는 현상이다.

## 스키마 초안

```sql
-- 1. 뉴스 소스 (레지스트리를 DB에도 반영)
CREATE TABLE `news_sources` (
    `id` SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(30) NOT NULL UNIQUE,      -- 'cointelegraph', 'coindesk', 'decrypt'
    `display_name` VARCHAR(50) NOT NULL,
    `feed_url` VARCHAR(500) NOT NULL,
    `category` ENUM('CRYPTO','MACRO') NOT NULL DEFAULT 'CRYPTO',
    `language` VARCHAR(10) NOT NULL DEFAULT 'en',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

-- 2. 기사 (제목·요약·링크까지만. 본문 전재 금지)
CREATE TABLE `news_articles` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `source_id` SMALLINT UNSIGNED NOT NULL,
    `url_hash` CHAR(64) NOT NULL UNIQUE,     -- 정규화 URL의 SHA-256. 에코 중복 차단
    `url` VARCHAR(1000) NOT NULL,
    `title` VARCHAR(500) NOT NULL,
    `summary` TEXT NULL,
    `published_at` TIMESTAMP NOT NULL,       -- 기사에 적힌 발행 시각
    `ingested_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 우리가 본 시각
    `symbols_json` JSON NULL,                -- 제목/요약에서 매칭한 심볼 ["BTC","ETH"]
    `topics_json` JSON NULL,                 -- ["FED","GEOPOLITICS","OIL"]
    FOREIGN KEY (`source_id`) REFERENCES `news_sources`(`id`),
    INDEX `idx_published` (`published_at`),
    INDEX `idx_ingested` (`ingested_at`)
) ENGINE=InnoDB;

-- 3. 감성 분류 (기사와 분리 — 모델을 바꿔 재분류해도 원문이 남는다)
CREATE TABLE `article_sentiments` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `article_id` BIGINT UNSIGNED NOT NULL,
    `classifier` VARCHAR(50) NOT NULL,       -- 'openai:<model>' | 'crowd'
    `stance` ENUM('BULLISH','BEARISH','NEUTRAL') NOT NULL,
    `confidence` DECIMAL(4,3) NULL,
    `classified_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_article_classifier` (`article_id`, `classifier`),
    FOREIGN KEY (`article_id`) REFERENCES `news_articles`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 4. 매크로 시계열 메타
CREATE TABLE `macro_series` (
    `id` SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(40) NOT NULL UNIQUE,      -- 'FED_FUNDS', 'UST_10Y', 'WTI', 'GOLD', 'DXY', 'CPI_YOY'
    `provider` VARCHAR(30) NOT NULL,         -- 'fred', 'ustreasury'
    `provider_series_id` VARCHAR(60) NOT NULL,
    `display_name` VARCHAR(100) NOT NULL,
    `unit` VARCHAR(20) NOT NULL,             -- 'percent', 'usd', 'index'
    `frequency` ENUM('DAILY','WEEKLY','MONTHLY','QUARTERLY') NOT NULL,
    `is_active` TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

-- 5. 매크로 관측치 — 개정(vintage)을 보존한다
CREATE TABLE `macro_observations` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `series_id` SMALLINT UNSIGNED NOT NULL,
    `period_date` DATE NOT NULL,             -- 값이 가리키는 기간 (2026-08 CPI → 2026-08-01)
    `released_at` TIMESTAMP NOT NULL,        -- 그 값이 공표된 시각 ★ 백테스트는 이걸 본다
    `value` DECIMAL(18,6) NOT NULL,
    `revision` SMALLINT UNSIGNED NOT NULL DEFAULT 0,  -- 0=최초 발표, 1+=개정
    `ingested_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_series_period_revision` (`series_id`, `period_date`, `revision`),
    FOREIGN KEY (`series_id`) REFERENCES `macro_series`(`id`) ON DELETE CASCADE,
    INDEX `idx_released` (`released_at`)
) ENGINE=InnoDB;

-- 6. 경제 캘린더
CREATE TABLE `macro_events` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(40) NOT NULL,             -- 'FOMC', 'CPI_RELEASE', 'NFP'
    `title` VARCHAR(200) NOT NULL,
    `scheduled_at` TIMESTAMP NOT NULL,
    `importance` ENUM('LOW','MEDIUM','HIGH') NOT NULL DEFAULT 'MEDIUM',
    `actual_value` DECIMAL(18,6) NULL,
    `forecast_value` DECIMAL(18,6) NULL,
    `ingested_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_code_scheduled` (`code`, `scheduled_at`),
    INDEX `idx_scheduled` (`scheduled_at`)
) ENGINE=InnoDB;

-- 7. 커뮤니티 — 유저 반응
CREATE TABLE `article_reactions` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `article_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `stance` ENUM('BULLISH','BEARISH','NEUTRAL') NOT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_article_user` (`article_id`, `user_id`),  -- 1인 1표, 변경은 UPDATE
    FOREIGN KEY (`article_id`) REFERENCES `news_articles`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 8. 커뮤니티 — 댓글
CREATE TABLE `article_comments` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `article_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `parent_id` BIGINT UNSIGNED NULL,        -- 1단계 대댓글
    `body` TEXT NOT NULL,
    `is_deleted` TINYINT(1) NOT NULL DEFAULT 0,  -- 하드 삭제하면 대댓글이 끊긴다
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (`article_id`) REFERENCES `news_articles`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`parent_id`) REFERENCES `article_comments`(`id`) ON DELETE CASCADE,
    INDEX `idx_article_created` (`article_id`, `created_at`)
) ENGINE=InnoDB;
```

## 커뮤니티가 부가기능이 아닌 이유

`article_reactions`의 유저 투표는 **두 번째 감성 소스**가 된다.

- AI 분류(`classifier='openai:...'`)와 군중 투표(`classifier='crowd'`)를 같은 테이블에서
  비교할 수 있다. 둘이 갈리는 기사가 곧 흥미로운 기사다.
- 이 데이터는 **우리 것**이라 저작권·약관 제약이 없다. 원문은 링크만 걸면서도
  고유 데이터 자산이 쌓인다.
- 유저가 남긴 투표의 사후 적중률을 추적하면 유저별 신뢰 가중치까지 만들 수 있다.

즉 뉴스 메뉴는 트래픽용 부가 기능이 아니라 **데이터 수집 장치**다.

## 코드 구조 — 기존 패턴을 그대로 따른다

`market/exchange_registry.py`가 잘 동작하므로 같은 모양으로 간다.
소스를 늘리는 일이 "레지스트리에 한 줄 추가"가 되어야 한다.

```
trading_engine/external/
├── news/
│   ├── registry.py       # NewsSourceSpec (code, feed_url, category, parser)
│   ├── rss_client.py     # RSS 파싱 + URL 정규화 + 중복 제거
│   └── classifier.py     # OpenAI 배치 분류 (url_hash 캐싱)
├── macro/
│   ├── registry.py       # MacroSeriesSpec (provider, series_id, unit, frequency)
│   ├── fred_client.py    # FRED / ALFRED(개정 이력)
│   └── treasury_client.py# 美재무부 금리곡선 CSV
└── point_in_time.py      # as_of(t) 조회 — 백테스트의 유일한 진입점
```

### `point_in_time.as_of(t)` 가 핵심이다

백테스트와 실시간 예측이 **같은 함수**로 데이터를 읽어야 한다.

```python
# t 시점에 "알 수 있었던" 것만 돌려준다
news = as_of.news(t, symbol="BTC", window="6h")      # ingested_at <= t
macro = as_of.macro(t, code="FED_FUNDS")             # released_at <= t, 최신 revision
events = as_of.upcoming_events(t, within="24h")      # scheduled_at > t
```

이 인터페이스를 먼저 만들어두면 Step 4 백테스트에서 룩어헤드가 구조적으로 불가능해진다.
반대로 이게 없으면 "released_at을 깜빡한" 쿼리 하나로 백테스트 전체가 무의미해진다.

## 소스 실측 (2026-09-02)

| 소스 | 상태 | 비고 |
| --- | --- | --- |
| Cointelegraph RSS | ✅ 200 | 키 불필요. `pubDate` 초 단위 |
| Decrypt RSS | ✅ 200 | 키 불필요 |
| CoinDesk RSS | ✅ 200 | 308 리다이렉트 추적 필요 |
| 美재무부 금리곡선 CSV | ✅ 200 | 키 불필요. 공식 |
| FRED | ⚠️ 키 필요 | **무료 발급.** 연준금리·CPI·유가·달러지수 + ALFRED 개정 이력 |
| CryptoPanic | ⚠️ 키 필요 | 무료 티어 한도 확인 필요 |
| Stooq | ❌ 차단 | CSV 대신 차단 페이지 반환. 쓰지 않는다 |
| GDELT | ❌ 연결 실패 | 재확인 필요 |

**금 시세**는 아직 확정 소스가 없다. FRED 키 발급 후 커버리지를 확인하고,
없으면 LBMA 등 공식 소스를 따로 찾는다.

## 알려진 한계 (수집 전에 인정하고 갈 것)

- **뉴스는 후행한다.** 5m/15m/1h horizon에서 기사는 대개 가격이 움직인 뒤에 나온다.
  예측력이 없을 가능성이 실재하고, 그래서 검증 전에 점수에 넣지 않는다.
- **심볼 귀속이 부정확하다.** RSS에 코인 태그가 없어 문자열 매칭에 의존한다.
  뉴스 대부분이 BTC·거시라 XRP/SOL/DOGE는 거의 안 잡힌다. 사실상 시장 전체 요인이다.
- **매크로는 주기가 느리다.** 연준금리는 하루 1회, CPI는 월 1회다.
  5분봉 신호에 직접 넣기보다 "국면(regime) 분류"에 쓰는 것이 맞을 수 있다.
- **홍보성 기사** — 소형 코인일수록 비중이 올라간다. 소스별 신뢰 가중치가 필요해질 수 있다.

## 단계

- **A. 수집·아카이브** — RSS + FRED + 재무부. 저장과 분류까지. **신호 영향 0.**
- **B. 뉴스 메뉴 + 커뮤니티** — 목록·상세(링크 아웃)·투표·댓글. Step 6 API / Step 9 UI 위에 얹는다.
- **C. 상관 검증** — Step 7의 `ai_signal_results`와 대조해 horizon별 예측력 측정.
- **D. 편입 여부 결정** — 근거가 나오면 스코어에. 안 나오면 **넣지 않는 것도 결과다.**

편입 방식은 두 가지다. `Final Score`는 Tech 45 + Consensus 20 + AI 20 + Risk 15 = 100이라
새 항목을 넣으려면 재배분이 필요하다. 그보다 **AI 20% 안에 컨텍스트로 넣는 쪽**이
스키마·가중치를 건드리지 않아 훨씬 싸다. D단계에서 정한다.

## OpenAI 호환 엔드포인트 (2026-09-02 확정)

이 프로젝트는 OpenAI 본사가 아니라 **카페24 LLM Router**(OpenAI 호환 프록시)를 쓴다.

- base_url: `https://llm-router.cafe24.com/api/v1`
- 키 형식: `sk-cafe24-` + 64 hex (74자)
- 콘솔·문서: https://llm-router.cafe24.com/docs

`OPENAI_BASE_URL` 을 비워두면 SDK 가 OpenAI 본사로 요청을 보내 `401 Incorrect API key`
가 된다. 키가 잘못된 것처럼 보이지만 원인은 엔드포인트다.

라우터는 OpenAI 외에 Anthropic·Google·DeepSeek 모델도 같은 인터페이스로 제공한다
(총 248개, `cafe24/auto` 자동 라우팅 포함). 모델을 바꿀 때 SDK 코드는 그대로 두고
모델명만 갈아끼우면 된다.
