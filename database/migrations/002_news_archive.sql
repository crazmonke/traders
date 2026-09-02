-- 002 — 뉴스 아카이브 (Step 11-a)
--
-- 대응: docs/EXTERNAL_DATA.md / ROADMAP.md "Step 11 외부 데이터 아카이브"
-- 매크로 시계열·캘린더 테이블은 Step 11-b 에서 003 으로 추가한다.
--
-- 원칙: 원문 본문은 저장하지 않는다. 제목·요약·링크까지만이다(저작권).
--       모든 레코드에 "사건 시각(published_at)"과 "우리가 본 시각(ingested_at)"을
--       따로 남긴다. 백테스트가 룩어헤드 없이 조회하려면 둘 다 필요하다.

CREATE TABLE IF NOT EXISTS `news_sources` (
    `id` SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `code` VARCHAR(30) NOT NULL UNIQUE,
    `display_name` VARCHAR(50) NOT NULL,
    `feed_url` VARCHAR(500) NOT NULL,
    `category` ENUM('CRYPTO', 'MACRO') NOT NULL DEFAULT 'CRYPTO',
    `language` VARCHAR(10) NOT NULL DEFAULT 'en',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2026-09-02 실측으로 응답을 확인한 피드만 넣는다.
-- CoinDesk 는 308 리다이렉트를 타므로 클라이언트가 리다이렉트를 따라가야 한다.
INSERT INTO `news_sources` (`code`, `display_name`, `feed_url`, `category`) VALUES
    ('cointelegraph', 'Cointelegraph', 'https://cointelegraph.com/rss', 'CRYPTO'),
    ('decrypt',       'Decrypt',       'https://decrypt.co/feed', 'CRYPTO'),
    ('coindesk',      'CoinDesk',      'https://www.coindesk.com/arc/outboundfeeds/rss/', 'CRYPTO')
ON DUPLICATE KEY UPDATE
    `display_name` = VALUES(`display_name`),
    `feed_url` = VALUES(`feed_url`);

CREATE TABLE IF NOT EXISTS `news_articles` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `source_id` SMALLINT UNSIGNED NOT NULL,
    -- 정규화한 URL 의 SHA-256. 매체가 서로 받아쓴 같은 기사를 한 건으로 묶는다.
    `url_hash` CHAR(64) NOT NULL UNIQUE,
    `url` VARCHAR(1000) NOT NULL,
    `title` VARCHAR(500) NOT NULL,
    `summary` TEXT NULL,
    `published_at` TIMESTAMP NOT NULL,
    `ingested_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `symbols_json` JSON NULL,
    `topics_json` JSON NULL,
    FOREIGN KEY (`source_id`) REFERENCES `news_sources`(`id`),
    INDEX `idx_published` (`published_at`),
    -- 백테스트는 "그 시각에 우리가 알고 있었는가"로 조회한다
    INDEX `idx_ingested` (`ingested_at`)
) ENGINE=InnoDB;

-- 감성은 기사와 분리한다. 분류기를 바꿔 다시 돌려도 원문이 남고,
-- AI 분류와 유저 투표(classifier='crowd')를 나란히 비교할 수 있다.
CREATE TABLE IF NOT EXISTS `article_sentiments` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `article_id` BIGINT UNSIGNED NOT NULL,
    `classifier` VARCHAR(50) NOT NULL,
    `stance` ENUM('BULLISH', 'BEARISH', 'NEUTRAL') NOT NULL,
    `confidence` DECIMAL(4, 3) NULL,
    `classified_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uq_article_classifier` (`article_id`, `classifier`),
    FOREIGN KEY (`article_id`) REFERENCES `news_articles`(`id`) ON DELETE CASCADE,
    INDEX `idx_stance` (`stance`, `classified_at`)
) ENGINE=InnoDB;
