"""Trading Engine 엔트리포인트.

현재 단계: Step 1 — Upbit WebSocket 수집 + Redis 캐싱 + 지표 계산.
룰 엔진(Step 2)은 engine.on_indicators() 로 붙인다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from trading_engine.config import settings
from trading_engine.indicators.calculator import Indicators
from trading_engine.indicators.engine import IndicatorEngine
from trading_engine.market.candle_feed import CandleFeed
from trading_engine.market.redis_store import RedisStore
from trading_engine.market.upbit_ws import UpbitWebSocketClient

log = logging.getLogger("trading_engine")


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def log_indicators(market: str, indicators: Indicators) -> None:
    """Step 2의 룰 엔진이 들어오기 전까지 자리를 지키는 기본 핸들러."""
    log.debug(
        "지표 갱신 market=%s close=%s rsi=%s macd_cross=%s ma_trend=%s imbalance=%s",
        market,
        indicators.close,
        indicators.rsi,
        indicators.macd_golden_cross,
        indicators.ma_trend,
        indicators.orderbook_imbalance,
    )


async def run() -> None:
    store = RedisStore()
    try:
        await store.ping()
        log.info("Redis 연결 확인 (%s:%s)", settings.redis_host, settings.redis_port)
    except Exception:
        log.exception("Redis 연결 실패 - 설정(.env)을 확인하라")
        raise

    feed = CandleFeed(settings.markets)
    seeded = await feed.seed_all()
    log.info("캔들 시딩 %d/%d 마켓", seeded, len(settings.markets))

    indicator_engine = IndicatorEngine(store, feed)
    indicator_engine.on_indicators(log_indicators)
    await indicator_engine.prime()

    client = UpbitWebSocketClient(store)
    client.on_event(indicator_engine.handle_event)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, client.stop)

    try:
        await client.run_forever()
    finally:
        await store.close()


def main() -> None:
    setup_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
