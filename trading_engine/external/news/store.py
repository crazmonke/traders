"""뉴스 아카이브 영속화."""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from trading_engine.database import mysql
from trading_engine.external.news.rss import Article

log = logging.getLogger(__name__)


async def active_sources() -> list[dict[str, Any]]:
    return await mysql.fetch_all(
        "SELECT id, code, display_name, feed_url, category "
        "FROM news_sources WHERE is_active = 1 ORDER BY id"
    )


async def save_articles(source_id: int, articles: Sequence[Article]) -> int:
    """새 기사만 넣고 넣은 건수를 돌려준다.

    `url_hash` UNIQUE + INSERT IGNORE 로 중복을 막는다. 매체가 같은 기사를
    다시 내보내도(RSS 는 매번 전량을 준다) 한 번만 쌓인다.

    ingested_at 은 DB 기본값(지금)을 쓴다. published_at 은 기사에 적힌 시각이다.
    둘을 분리해야 백테스트가 "그 시각에 우리가 알 수 있었는가"를 판정할 수 있다.
    """
    if not articles:
        return 0

    rows = [
        (
            source_id,
            article.url_hash,
            article.url[:1000],
            article.title,
            article.summary,
            article.published_at.strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(article.symbols),
            json.dumps(article.topics),
        )
        for article in articles
    ]
    return await mysql.execute_many(
        "INSERT IGNORE INTO news_articles "
        "(source_id, url_hash, url, title, summary, published_at, symbols_json, topics_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        rows,
    )


async def unclassified(classifier: str, limit: int = 50) -> list[dict[str, Any]]:
    """아직 이 분류기로 분류하지 않은 기사. 최신 것부터."""
    return await mysql.fetch_all(
        "SELECT a.id, a.title, a.summary, a.symbols_json "
        "FROM news_articles a "
        "LEFT JOIN article_sentiments s "
        "  ON s.article_id = a.id AND s.classifier = %s "
        "WHERE s.id IS NULL "
        "ORDER BY a.published_at DESC LIMIT %s",
        (classifier, limit),
    )


async def save_sentiment(
    article_id: int, classifier: str, stance: str, confidence: float | None
) -> None:
    """같은 (기사, 분류기) 조합은 덮어쓴다. 모델을 다시 돌려도 중복이 안 쌓인다."""
    await mysql.execute(
        "INSERT INTO article_sentiments (article_id, classifier, stance, confidence) "
        "VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE stance = VALUES(stance), "
        "  confidence = VALUES(confidence), classified_at = CURRENT_TIMESTAMP",
        (article_id, classifier, stance, confidence),
    )


async def archive_stats() -> dict[str, Any]:
    row = await mysql.fetch_one(
        "SELECT COUNT(*) AS articles, "
        "  MIN(published_at) AS oldest, MAX(published_at) AS newest "
        "FROM news_articles"
    )
    return row or {}
