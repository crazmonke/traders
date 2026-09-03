"""백테스트 작업 워커 — PHP API 가 넣은 요청을 꺼내 실행한다. (Step 6-a)

    PHP  POST /api/v1/backtest/run  →  Redis 리스트 `backtest:queue`  →  이 워커

**동기 실행이 불가능해서 큐로 나눴다.** 한 번 돌리는 데 실측 16초~수 분이 걸린다
(1시간봉 2000봉 재생). HTTP 요청을 붙잡고 있으면 타임아웃이 나고, 클라이언트 재시도가
같은 백테스트를 중복 실행한다.

수집 태스크와 같은 이벤트 루프에서 돌지만, **백테스트 자체는 CPU 를 오래 쓴다.**
`asyncio.to_thread` 로 옮겨 시세 수집이 멈추지 않게 한다 — 옮기지 않으면 백테스트가
도는 몇 분 동안 거래소 WebSocket 이 전부 지연된다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from trading_engine.backtest import costs as costs_mod
from trading_engine.backtest import data as data_mod
from trading_engine.backtest import store as backtest_store
from trading_engine.backtest.costs import GLOBAL_CONSENSUS
from trading_engine.backtest.engine import Backtester, BacktestParams
from trading_engine.backtest.runner import UPBIT_CODE, collect
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

QUEUE_KEY = "backtest:queue"
DAY_MS = 24 * 60 * 60 * 1000

# 큐가 비었을 때 이만큼 기다렸다 다시 본다. BLPOP 을 쓰지 않는 이유는 그 커넥션이
# 블로킹 동안 다른 용도로 못 쓰이고, 종료 신호에도 늦게 반응하기 때문이다.
POLL_INTERVAL_SEC = 2.0


async def run_forever(store: RedisStore) -> None:
    """큐를 계속 지켜본다. 한 작업이 실패해도 워커는 죽지 않는다."""
    log.info("백테스트 워커 대기 (%s)", QUEUE_KEY)
    while True:
        try:
            raw = await store.client.lpop(QUEUE_KEY)
        except Exception:
            log.exception("백테스트 큐 조회 실패")
            raw = None

        if not raw:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue

        try:
            await handle(json.loads(raw))
        except Exception:
            # 작업 하나가 깨져도 다음 작업은 처리해야 한다.
            log.exception("백테스트 작업 실패: %s", raw)


async def handle(job: dict[str, Any]) -> None:
    """작업 하나를 실행하고 `backtest_logs` 에 저장한다."""
    symbol = str(job["symbol"])
    reference = str(job.get("reference_exchange", GLOBAL_CONSENSUS))
    timeframe = str(job.get("timeframe", "1h"))
    days = int(job.get("days", 90))
    user_id = int(job["user_id"])

    started = time.monotonic()
    log.info(
        "백테스트 시작 job=%s %s [%s] %s %d일",
        job.get("job_id"), symbol, reference, timeframe, days,
    )

    until = int(time.time() * 1000)
    per_exchange = await collect(
        symbol,
        until - days * DAY_MS,
        until,
        timeframe=timeframe,
        # 실전용은 기준가 거래소가 빠지면 체결할 가격이 없다.
        keep=UPBIT_CODE if reference == UPBIT_CODE else None,
    )

    params = BacktestParams(
        symbol=symbol, reference_exchange=reference, timeframe=timeframe
    )
    # 재생은 CPU 작업이다. 이벤트 루프에서 직접 돌리면 시세 수집이 그동안 멈춘다.
    result = await asyncio.to_thread(_replay, per_exchange, params)

    log_id = await backtest_store.save(result, user_id)
    log.info(
        "백테스트 완료 job=%s id=%s 거래 %d건 수익률 %.2f%% (%.0f초)",
        job.get("job_id"),
        log_id,
        result.metrics.total_trades,
        result.metrics.total_return_pct,
        time.monotonic() - started,
    )


def _replay(per_exchange: dict[str, list[dict[str, Any]]], params: BacktestParams):
    """스레드에서 도는 부분. 네트워크·DB 를 건드리지 않는다."""
    if params.reference_exchange == GLOBAL_CONSENSUS:
        price_series = data_mod.global_price_series(per_exchange)
    else:
        price_series = data_mod.single_exchange_series(
            per_exchange, params.reference_exchange
        )
    cost_model = costs_mod.for_reference_exchange(params.reference_exchange)

    return Backtester(params, cost_model).run(per_exchange, price_series)
