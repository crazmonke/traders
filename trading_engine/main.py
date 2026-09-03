"""Trading Engine 엔트리포인트.

수집·지표(Step 1) → 합의·룰 엔진(2-a) → AI 분석·저장·publish(2-b) 가 본류이고,
웹훅 수신(3-b)·백테스트 워커(6-a)·뉴스 수집(11-a)·성과 추적(7)이 같은 이벤트 루프에서
독립 태스크로 붙는다. **부가 태스크는 전부 꺼도 수집·신호는 그대로 동작한다.**
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from trading_engine.config import settings
from trading_engine.backtest import worker as backtest_worker
from trading_engine.external.news import collector as news_collector
from trading_engine.external.tradingview import receiver as webhook_receiver
from trading_engine.indicators.calculator import Indicators
from trading_engine.indicators.engine import IndicatorEngine
from trading_engine.market.exchange_feed import ExchangeFeed
from trading_engine.market.exchange_registry import resolve_specs
from trading_engine.market.market_manager import MarketManager, base_of
from trading_engine.market.redis_store import RedisStore
from trading_engine.strategy.signal_engine import SignalEngine
from trading_engine.strategy.signal_pipeline import SignalPipeline
from trading_engine.tracking import result_tracker

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
    signal_engine = SignalEngine(manager, store)
    pipeline = SignalPipeline(signal_engine, store)

    async def on_indicators(exchange: str, symbol: str, indicators: Indicators) -> None:
        """거래소 지표가 갱신될 때마다 글로벌 집계와 합의를 다시 낸다."""
        manager.record_indicators(exchange, symbol, indicators)
        base = base_of(symbol)
        snapshot = await manager.publish(base)
        if snapshot:
            log.debug(
                "글로벌 갱신 %s price=%s sources=%d rsi=%s",
                snapshot["symbol"],
                snapshot["price"],
                snapshot["source_count"],
                snapshot["rsi"],
            )
        # 집계가 끝난 뒤에 평가해야 같은 스냅샷을 본다. 자체 스로틀이 있어
        # 거래소 수만큼 중복 계산되지는 않는다.
        evaluation = await pipeline.run(base)
        if evaluation:
            log.info(
                "신호 %s %s final=%.1f tech=%.1f consensus=%.0f%% (%d개 거래소)%s",
                evaluation.symbol,
                evaluation.signal_type,
                evaluation.final_score,
                evaluation.tech.score,
                evaluation.consensus.pct,
                evaluation.consensus.valid_count,
                f" AI={evaluation.ai_score:.0f}" if evaluation.ai_score is not None else "",
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

    log.info("AI 호출 예산: %s", pipeline.budget.describe())
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

    # 유저별 트레이딩뷰 웹훅 수신(Step 3-b). PRO 부가 기능이라 꺼도 나머지는 돌아야 한다.
    if settings.webhook_enabled:
        tasks.append(webhook_receiver.serve(webhook_receiver.create_app(store)))
        log.info(
            "웹훅 수신 대기 %s:%s (분당 %s회 제한)",
            settings.webhook_host,
            settings.webhook_port,
            settings.webhook_rate_per_min,
        )
    else:
        log.info("웹훅 수신 비활성 (WEBHOOK_ENABLED=0)")

    # 백테스트 워커(Step 6-a). API 요청을 큐에서 꺼내 돌린다.
    # 재생은 CPU 작업이라 워커 안에서 스레드로 옮긴다 - 수집이 멈추지 않게.
    if settings.backtest_worker_enabled:
        tasks.append(backtest_worker.run_forever(store))
    else:
        log.info("백테스트 워커 비활성 (BACKTEST_WORKER_ENABLED=0)")

    # 뉴스 아카이브(Step 11-a). 신호 점수에는 영향을 주지 않는다.
    # 수집이 실패해도 예외를 밖으로 내지 않으므로 시세 수집을 멈추지 않는다.
    # NEWS_POLL_SEC=0 으로 끌 수 있다.
    if settings.news_poll_sec > 0:
        tasks.append(news_collector.run_forever())
    else:
        log.info("뉴스 수집 비활성 (NEWS_POLL_SEC=0)")

    # 시그널 성과 추적(Step 7). 서버에서는 별도 systemd 유닛
    # (`deploy/systemd/ai-trading-scheduler.service`)으로 띄우므로 기본값은 꺼짐이다.
    # 추적기가 거래소 캔들을 받아오는 동안 수집이 멈추면 안 되기 때문에 프로세스를 나눈다.
    # 둘 다 켜져도 Redis 분산 락이 중복 실행을 막는다.
    if settings.tracker_in_engine:
        tasks.append(result_tracker.run_forever(store))
        log.info("성과 추적 동거 실행 (TRACKER_IN_ENGINE=1, %d초 주기)", settings.tracker_interval_sec)

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
