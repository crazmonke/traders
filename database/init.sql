-- AI Trading Platform - Initial DB schema
-- prompt.md 2절(DB 스키마) 그대로 반영. docker-compose MySQL 컨테이너 최초 기동 시 자동 실행됩니다.

CREATE DATABASE IF NOT EXISTS `ai_trading` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `ai_trading`;

-- 1. 사용자 테이블
CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `email` VARCHAR(191) NOT NULL UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `name` VARCHAR(50) NOT NULL,
    `role` ENUM('user', 'admin') DEFAULT 'user',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2. 구독 정보 테이블
CREATE TABLE IF NOT EXISTS `subscriptions` (
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
CREATE TABLE IF NOT EXISTS `ai_signals` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `market` VARCHAR(20) NOT NULL,
    `timeframe` VARCHAR(10) NOT NULL,
    `signal_type` ENUM('STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL') NOT NULL,
    `tech_score` TINYINT UNSIGNED NOT NULL,
    `ai_score` TINYINT UNSIGNED NULL,
    `risk_score` TINYINT UNSIGNED NOT NULL,
    `final_score` TINYINT UNSIGNED NOT NULL,
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
CREATE TABLE IF NOT EXISTS `ai_signal_results` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `signal_id` BIGINT UNSIGNED NOT NULL,
    `horizon` ENUM('5m', '15m', '1h') NOT NULL,
    `price_entry` DECIMAL(18, 8) NOT NULL,
    `price_after` DECIMAL(18, 8) NULL,
    `return_pct` DECIMAL(6, 2) NULL,
    `is_accurate` TINYINT(1) DEFAULT NULL,
    `evaluated_at` TIMESTAMP NULL,
    UNIQUE KEY `uq_signal_horizon` (`signal_id`, `horizon`),
    FOREIGN KEY (`signal_id`) REFERENCES `ai_signals`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 5. 백테스트 이력 테이블
CREATE TABLE IF NOT EXISTS `backtest_logs` (
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

-- 6. 실거래 리스크 관리 테이블
CREATE TABLE IF NOT EXISTS `trading_safety_state` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `mode` ENUM('PAPER', 'LIVE') NOT NULL DEFAULT 'PAPER',
    `daily_loss_limit_pct` DECIMAL(5, 2) NOT NULL DEFAULT 3.00,
    `max_position_size_krw` DECIMAL(18, 2) NOT NULL,
    `kill_switch_active` TINYINT(1) NOT NULL DEFAULT 0,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;
