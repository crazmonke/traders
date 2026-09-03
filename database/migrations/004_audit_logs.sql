-- ---------------------------------------------------------------------------
-- 004 — 감사 로그 (Step 6-b, Step 5 가 요구)
--
-- Step 5 DoD 는 "LIVE 전환은 명시적 조작 + 감사 로그를 남긴다"를 요구하는데,
-- init.sql 6개 테이블 어디에도 남길 곳이 없다. 실거래 설정을 누가 언제 바꿨는지
-- 모르면 사고가 났을 때 원인을 찾을 수 없다.
--
-- `user_id` 는 ON DELETE SET NULL 이다. 유저를 지웠다고 "누가 LIVE 를 켰었나"
-- 기록까지 사라지면 감사 로그의 의미가 없다. 계정은 사라져도 행위는 남는다.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `audit_logs` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT UNSIGNED NULL,
    `action` VARCHAR(50) NOT NULL,
    `target_type` VARCHAR(50) NULL,
    `target_id` BIGINT UNSIGNED NULL,
    `detail_json` JSON NULL,
    `ip` VARCHAR(45) NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    INDEX `idx_user_created` (`user_id`, `created_at`),
    INDEX `idx_action_created` (`action`, `created_at`)
) ENGINE=InnoDB;
