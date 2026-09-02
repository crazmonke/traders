"""Trading Engine 엔트리포인트.

현재 단계: Step 1 — ccxt 다중 거래소 수집 + 거래소별 지표 + 글로벌 가중 평균.
Step 2(Consensus·RuleEngine·OpenAI)는 indicator_engine.on_indicators() 로 붙인다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from trading_engine.config import settings
from trading_engine.external.news import collector as news_collector
from trading_engine.indicators.calculator import Indicators
from trading_engine.indicators.engine import IndicatorEngine
from trading_engine.market.exchange_feed import ExchangeFeed
from trading_engine.market.exchange_registry import resolve_specs
from trading_engine.market.market_manager import MarketManager, base_of
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def run() -> None:
    store = RedisStore()
    try:
        await store.ping()
        log.info("Redis 연결 확인 (%s:%s)", settings.redis_host, settings.redis_port)
    except Exception:
        log.exception("Redis 연결 실패 - 설정(.env)을 확인하라")
        raise

    manager = MarketManager(store)
    indicator_engine = IndicatorEngine(store)

    async def on_indicators(exchange: str, symbol: str, indicators: Indicators) -> None:
        """거래소 지표가 갱신될 때마다 글로벌 집계를 다시 낸다."""
        manager.record_indicators(exchange, symbol, indicators)
        snapshot = await manager.publish(base_of(symbol))
        if snapshot:
            log.debug(
                "글로벌 갱신 %s price=%s sources=%d rsi=%s",
                snapshot["symbol"],
                snapshot["price"],
                snapshot["source_count"],
                snapshot["rsi"],
            )

    indicator_engine.on_indicators(on_indicators)

    async def on_market_event(event_type: str, exchange: str, symbol: str, payload: dict) -> None:
        # 가중치(24h 거래대금)는 ticker 에서만 온다.
        if event_type == "ticker":
            manager.record_ticker(exchange, symbol, payload)
        await indicator_engine.handle_event(event_type, exchange, symbol, payload)

    # 거래소 코드 오타는 여기서 즉시 걸린다. 수집을 시작한 뒤에 알면 늦다.
    feeds = [
        ExchangeFeed(spec, store, settings.symbols) for spec in resolve_specs(settings.exchanges)
    ]
    for feed in feeds:
        feed.on_event(on_market_event)

    log.info(
        "수집 거래소: %s / 심볼: %s",
        ", ".join(feed.code for feed in feeds),
        ", ".join(settings.symbols),
    )

    def stop_all() -> None:
        for feed in feeds:
            feed.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_all)

    tasks = [feed.run() for feed in feeds]

    # 뉴스 아카이브(Step 11-a). 신호 점수에는 영향을 주지 않는다.
    # 수집이 실패해도 예외를 밖으로 내지 않으므로 시세 수집을 멈추지 않는다.
    # NEWS_POLL_SEC=0 으로 끌 수 있다.
    if settings.news_poll_sec > 0:
        tasks.append(news_collector.run_forever())
    else:
        log.info("뉴스 수집 비활성 (NEWS_POLL_SEC=0)")

    try:
        # 거래소별로 독립된 태스크다. 하나가 죽어도 나머지는 계속 수집한다.
        await asyncio.gather(*tasks)
    finally:
        await store.close()


def main() -> None:
    setup_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
