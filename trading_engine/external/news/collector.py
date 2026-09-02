"""뉴스 수집 루프.

**신호 점수에 영향을 주지 않는다.** 이 단계의 목적은 아카이브 축적뿐이다.
과거 기사는 나중에 소급 확보할 수 없으므로 검증보다 수집이 먼저다.
(docs/EXTERNAL_DATA.md)
"""

from __future__ import annotations

import asyncio
import logging

from trading_engine.config import settings
from trading_engine.external.news import classifier, rss, store

log = logging.getLogger(__name__)

# 수집이 실패해도 엔진은 계속 돈다. 실패 시 이만큼 쉬었다 다시 시도한다.
RETRY_SEC = 60.0


async def collect_once() -> int:
    """모든 활성 피드를 한 바퀴 돌고 새로 저장한 기사 수를 돌려준다."""
    sources = await store.active_sources()
    if not sources:
        log.warning("활성 뉴스 소스가 없다 - 수집을 건너뛴다")
        return 0

    saved_total = 0
    async with rss.make_session() as session:
        for source in sources:
            articles = await rss.fetch_feed(session, source["feed_url"])
            if not articles:
                continue
            saved = await store.save_articles(source["id"], articles)
            saved_total += saved
            log.info(
                "%s: 수신 %d건 / 신규 %d건", source["code"], len(articles), saved
            )
    return saved_total


async def run_forever(poll_sec: int | None = None) -> None:
    """엔진과 함께 도는 백그라운드 태스크.

    시세 수집과 완전히 분리돼 있다. 여기서 무슨 일이 나도 예외를 밖으로
    내보내지 않는다 — 뉴스 때문에 거래소 수집이 멈추면 안 된다.
    """
    interval = poll_sec or settings.news_poll_sec
    log.info("뉴스 수집 시작 (주기 %d초)", interval)
    while True:
        try:
            saved = await collect_once()
            # 분류는 실패해도(키 없음·크레딧 없음) 0을 돌려주고 수집은 계속된다.
            classified = await classifier.classify_pending()
            stats = await store.archive_stats()
            log.info(
                "뉴스 수집 완료: 신규 %d건 / 분류 %d건 (아카이브 누적 %s건)",
                saved,
                classified,
                stats.get("articles", "?"),
            )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("뉴스 수집을 종료한다")
            raise
        except Exception:
            log.exception("뉴스 수집 실패 - %.0f초 후 재시도", RETRY_SEC)
            await asyncio.sleep(RETRY_SEC)
