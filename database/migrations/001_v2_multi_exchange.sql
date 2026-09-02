-- 001 — v1(업비트 단일) → v2(다중 거래소) 스키마 전환
--
-- 대응: ROADMAP.md "Step 0 — DB 마이그레이션" DoD / prompt.md v2 §2 DDL
-- 전제: 운영 데이터가 아직 없다. 그래서 NOT NULL 컬럼을 기본값 없이 추가하고
--       롤백 스크립트도 두지 않는다. (ROADMAP.md Step 0 명시)

-- ---------------------------------------------------------------------------
-- 1. 거래소 메타 테이블 (신규)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `exchanges` (
    `id` SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(20) NOT NULL UNIQUE,
    `display_name` VARCHAR(50) NOT NULL,
    `supports_orderbook` TINYINT(1) DEFAULT 1,
    `is_private_trading_target` TINYINT(1) DEFAULT 0,
    `rate_limit_ms` INT UNSIGNED NOT NULL DEFAULT 1000,
    `is_active` TINYINT(1) DEFAULT 1,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 수집 대상 5개 거래소. 자동매매 실행처는 업비트뿐이다(prompt.md v2 §5).
-- rate_limit_ms 는 DDL 기본값 1000 으로 두고, Step 1-a 에서 ccxt 의 실제
-- `exchange.rateLimit` 값으로 동기화한다. (여기서 임의값을 박아두지 않는다)
INSERT INTO `exchanges` (`code`, `display_name`, `supports_orderbook`, `is_private_trading_target`)
VALUES
    ('binance',  'Binance',  1, 0),
    ('okx',      'OKX',      1, 0),
    ('bybit',    'Bybit',    1, 0),
    ('coinbase', 'Coinbase', 1, 0),
    ('upbit',    'Upbit',    1, 1)
ON DUPLICATE KEY UPDATE
    `display_name` = VALUES(`display_name`),
    `is_private_trading_target` = VALUES(`is_private_trading_target`);

-- ---------------------------------------------------------------------------
-- 2. users.locale (i18n, Step 10)
-- ---------------------------------------------------------------------------
ALTER TABLE `users`
    ADD COLUMN `locale` VARCHAR(10) NOT NULL DEFAULT 'ko' AFTER `role`;

-- ---------------------------------------------------------------------------
-- 3. 유저별 TradingView 웹훅 (신규, 멀티테넌트 PRO)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `user_webhooks` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `webhook_token` CHAR(43) NOT NULL UNIQUE,
    `label` VARCHAR(100) NULL,
    `is_active` TINYINT(1) DEFAULT 1,
    `last_received_at` TIMESTAMP NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `revoked_at` TIMESTAMP NULL,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    INDEX `idx_token` (`webhook_token`)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- 4. ai_signals — 거래소 무관 심볼 + 지표 4종 + Consensus
-- ---------------------------------------------------------------------------
-- entry_price 는 v2 에 없다. 글로벌 거래량 가중 평균가가 그 자리를 대신하므로
-- 새로 만들지 않고 이름만 바꾼다. (그대로 두면 NOT NULL 인데 아무도 안 쓰는
-- 컬럼이 남아 모든 INSERT 가 실패한다)
ALTER TABLE `ai_signals`
    RENAME COLUMN `market` TO `symbol`,
    RENAME COLUMN `entry_price` TO `entry_price_global`,
    ADD COLUMN `entry_price_upbit` DECIMAL(18, 8) NULL AFTER `entry_price_global`,
    ADD COLUMN `bollinger_position`
        ENUM('BELOW_LOWER', 'LOWER_HALF', 'UPPER_HALF', 'ABOVE_UPPER') NULL AFTER `macd_val`,
    ADD COLUMN `stochastic_k` DECIMAL(6, 2) NULL AFTER `bollinger_position`,
    ADD COLUMN `stochastic_d` DECIMAL(6, 2) NULL AFTER `stochastic_k`,
    ADD COLUMN `adx_val` DECIMAL(6, 2) NULL AFTER `stochastic_d`,
    ADD COLUMN `cci_val` DECIMAL(8, 2) NULL AFTER `adx_val`,
    ADD COLUMN `exchange_consensus_pct` DECIMAL(5, 2) NOT NULL AFTER `volume_change_pct`,
    ADD COLUMN `data_sources_json` JSON NOT NULL AFTER `exchange_consensus_pct`;

ALTER TABLE `ai_signals`
    DROP INDEX `idx_market_created`,
    ADD INDEX `idx_symbol_created` (`symbol`, `created_at`),
    ADD INDEX `idx_consensus` (`exchange_consensus_pct`);

-- ---------------------------------------------------------------------------
-- 5. external_signals (신규) — ai_signals 와 user_webhooks 를 모두 참조하므로
--    두 테이블이 준비된 뒤에 만든다.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `external_signals` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `user_webhook_id` BIGINT UNSIGNED NOT NULL,
    `source` ENUM('TRADINGVIEW_WEBHOOK') NOT NULL DEFAULT 'TRADINGVIEW_WEBHOOK',
    `symbol` VARCHAR(20) NOT NULL,
    `action` ENUM('BUY', 'SELL', 'EXIT') NOT NULL,
    `strategy_name` VARCHAR(100) NULL,
    `raw_payload_json` JSON NOT NULL,
    `linked_signal_id` BIGINT UNSIGNED NULL,
    `received_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`user_webhook_id`) REFERENCES `user_webhooks`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`linked_signal_id`) REFERENCES `ai_signals`(`id`) ON DELETE SET NULL,
    INDEX `idx_user_symbol` (`user_id`, `symbol`, `received_at`)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- 6. backtest_logs — 신호검증용/업비트 실전용 구분 (Step 4)
-- ---------------------------------------------------------------------------
ALTER TABLE `backtest_logs`
    RENAME COLUMN `market` TO `symbol`,
    ADD COLUMN `reference_exchange` VARCHAR(20) NOT NULL AFTER `symbol`;
