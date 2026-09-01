"""Trading Engine 엔트리포인트.

현재 단계: Step 1-a — Upbit WebSocket 수집 + Redis 캐싱.
지표 계산(Step 1-b)·룰 엔진(Step 2)은 client.on_event() 로 붙인다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from trading_engine.config import settings
from trading_engine.market.redis_store import RedisStore
from trading_engine.market.upbit_ws import UpbitWebSocketClient

log = logging.getLogger("trading_engine")


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

    client = UpbitWebSocketClient(store)

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
