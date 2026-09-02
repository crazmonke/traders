"""Trading Engine 엔트리포인트.

현재 단계: Step 1-a — ccxt 다중 거래소 수집 + Upbit 지표 계산.

수집 경로가 둘이다. Upbit 은 전용 WebSocket(캔들·지표까지), 나머지 거래소는
ccxt ExchangeFeed(ticker·orderbook 만). Step 1-b 에서 지표를 거래소별로 돌리면서
Upbit 도 ccxt 경로로 합친다 — 그래서 지금은 EXCHANGES 기본값에 upbit 이 없다
(넣으면 같은 데이터를 두 번 수집한다).
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
from trading_engine.market.exchange_feed import ExchangeFeed
from trading_engine.market.exchange_registry import resolve_specs
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

    # 거래소 코드 오타는 여기서 즉시 걸린다. 수집을 시작한 뒤에 알면 늦다.
    exchange_feeds = [
        ExchangeFeed(spec, store, settings.symbols)
        for spec in resolve_specs(settings.exchanges)
    ]
    if exchange_feeds:
        log.info(
            "ccxt 수집 거래소: %s (심볼 %s)",
            ", ".join(feed.code for feed in exchange_feeds),
            ", ".join(settings.symbols),
        )

    def stop_all() -> None:
        client.stop()
        for feed in exchange_feeds:
            feed.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_all)

    try:
        # 거래소별로 독립된 태스크다. 하나가 죽어도 나머지는 계속 수집한다.
        await asyncio.gather(
            client.run_forever(),
            *[feed.run() for feed in exchange_feeds],
        )
    finally:
        await store.close()


def main() -> None:
    setup_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
