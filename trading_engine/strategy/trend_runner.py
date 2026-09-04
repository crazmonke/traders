"""추세추종 신호 생성·저장 데몬.

### 왜 별도 경로인가

룰 엔진 신호(`signal_pipeline`)와 **완전히 다른 규칙**이라 같은 파이프라인에 얹으면
둘 다 읽기 어려워진다. `scoring_version` 이 다르므로 적중률 화면에서 자동으로
분리되고(`GROUP BY scoring_version`), 어느 쪽이 실제로 맞았는지 나란히 비교된다.

### 하루 한 번, 봉이 닫힌 뒤에만

일봉은 UTC 자정에 닫힌다. 닫히기 전 값으로 판정하면 그날 종가가 아직 움직이는데
신호를 내는 것이라, 백테스트와 다른 것을 재게 된다. **직전에 닫힌 봉**만 본다.

같은 날 두 번 내보내지 않도록 Redis 로 하루치 슬롯을 선점한다 — 성과 추적기와
같은 방식이다(`tracking/result_tracker.py`).

### 교차한 날에만 신호를 낸다

상태(위/아래)만 보고 매일 내보내면 하루 5건씩 쌓여 "주 1~2건" 이 아니게 되고,
적중률 통계도 같은 포지션을 수백 번 센 것이 된다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Sequence

from trading_engine.backtest import data as backtest_data
from trading_engine.backtest.runner import collect
from trading_engine.config import settings
from trading_engine.database import mysql
from trading_engine.market.redis_store import RedisStore
from trading_engine.strategy import trend
from trading_engine.strategy.trend import TrendState

log = logging.getLogger(__name__)

DAY_MS = 86_400_000

# 이평 계산에 필요한 봉 + 여유. 거래소가 일부 봉을 빠뜨려도 창이 차도록 넉넉히 받는다.
FETCH_DAYS = trend.MA_DAYS * 3

LOCK_NAME = "trend-signals"

INSERT_SQL = (
    "INSERT INTO ai_signals ("
    "symbol, timeframe, scoring_version, signal_type, tech_score, risk_score, final_score, "
    "entry_price_global, exchange_consensus_pct, data_sources_json"
    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def data_sources_json(state: TrendState, exchanges: Sequence[str]) -> str:
    """이 신호가 어떻게 만들어졌는지. 나중에 재현하려면 이 값들이 있어야 한다."""
    return json.dumps(
        {
            "strategy": "trend-following",
            "scoring_version": trend.STRATEGY_VERSION,
            "timeframe": trend.TIMEFRAME,
            "ma_days": trend.MA_DAYS,
            "close": round(state.close, 8),
            "moving_average": round(state.moving_average, 8),
            "distance_pct": round(state.distance_pct, 4),
            "candle_ts": state.candle_ts,
            "exchanges": sorted(exchanges),
            # 룰 엔진 신호가 아니다. 화면·통계에서 섞이면 안 된다.
            "rule_engine": False,
        },
        ensure_ascii=False,
    )


async def save(state: TrendState, exchanges: Sequence[str]) -> int | None:
    """`ai_signals` 에 한 행. 실패해도 예외를 밖으로 내지 않는다."""
    score = trend.ENTER_SCORE if state.above else trend.EXIT_SCORE
    try:
        return await mysql.execute(
            INSERT_SQL,
            (
                state.symbol,
                trend.TIMEFRAME,
                trend.STRATEGY_VERSION,
                state.signal_type,
                int(score),
                # 이 전략에는 위험 점수 개념이 없다. 중립값을 넣되 화면에서
                # 룰 엔진의 위험 점수와 같은 것으로 읽히지 않도록 문서에 남긴다.
                int(trend.NEUTRAL_SCORE),
                int(score),
                round(state.close, 8),
                round(state.agreement_pct, 2),
                data_sources_json(state, exchanges),
            ),
        )
    except Exception:
        log.exception("추세 신호 저장 실패 (%s)", state.symbol)
        return None


async def evaluate(symbol: str) -> tuple[TrendState | None, list[str]]:
    """한 심볼의 현재 추세 상태. 캔들 수집이 실패하면 (None, [])."""
    until = int(time.time() * 1000)
    since = until - FETCH_DAYS * DAY_MS
    try:
        per_exchange = await collect(symbol, since, until, timeframe=trend.TIMEFRAME)
    except Exception:
        log.exception("추세 판정용 캔들 수집 실패 (%s)", symbol)
        return None, []

    if not per_exchange:
        return None, []

    series = backtest_data.global_price_series(per_exchange)
    state = trend.state_from(symbol, series, per_exchange)
    if state is None:
        log.info("%s 봉이 모자라 추세를 판정하지 않는다 (%d개)", symbol, len(series))

    return state, sorted(per_exchange)


async def run_once(store: RedisStore, symbols: Sequence[str] | None = None) -> int:
    """모든 심볼을 한 번 훑는다. 내보낸 신호 수를 돌려준다."""
    emitted = 0
    for symbol in symbols or settings.symbols:
        state, exchanges = await evaluate(symbol)
        if state is None:
            continue

        if not state.crossed:
            log.debug(
                "%s 추세 유지 (%s, 이평 대비 %+.1f%%)",
                symbol,
                "위" if state.above else "아래",
                state.distance_pct,
            )
            continue

        # 같은 봉으로 두 번 내보내지 않는다. 키에 봉 시각을 넣어 하루가 지나면
        # 자연히 새 슬롯이 된다.
        claimed = await claim(store, symbol, state.candle_ts)
        if not claimed:
            continue

        signal_id = await save(state, exchanges)
        log.info(
            "추세 신호 %s %s (종가 %.2f, %d일 이평 %.2f, %+.1f%%, 거래소 합의 %.0f%%) id=%s",
            symbol,
            state.signal_type,
            state.close,
            trend.MA_DAYS,
            state.moving_average,
            state.distance_pct,
            state.agreement_pct,
            signal_id,
        )
        emitted += 1

    return emitted


async def claim(store: RedisStore, symbol: str, candle_ts: int) -> bool:
    """이 봉의 신호 슬롯을 선점한다. Redis 가 흔들리면 내보내지 않는다 —
    중복 신호가 적중률 통계를 부풀리는 것이 더 나쁘다."""
    try:
        return await store.claim_signal_record(
            f"trend:{symbol}", str(candle_ts), ttl=int(DAY_MS / 1000 * 2)
        )
    except Exception:
        log.exception("추세 신호 슬롯 선점 실패 (%s)", symbol)
        return False


async def run_forever(store: RedisStore, interval: int | None = None) -> None:
    """주기적으로 추세를 확인한다.

    일봉은 하루 한 번 닫히므로 자주 볼 이유가 없다. 그래도 시간 단위로 도는 이유는
    프로세스가 재시작돼도 그날 안에 다시 판정하기 위해서다(슬롯이 중복을 막는다).
    """
    interval = interval or settings.trend_interval_sec
    log.info(
        "추세추종 시작 (%s %d일 이평, %d초 주기, 심볼 %s)",
        trend.TIMEFRAME,
        trend.MA_DAYS,
        interval,
        ", ".join(settings.symbols),
    )

    while True:
        started = time.monotonic()
        try:
            token = await store.claim_lock(LOCK_NAME, ttl=interval)
            if token is not None:
                try:
                    await run_once(store)
                finally:
                    await store.release_lock(LOCK_NAME, token)
        except Exception:
            log.exception("추세 판정 주기 실패 - 다음 주기에 다시 시도한다")

        await asyncio.sleep(max(interval - (time.monotonic() - started), 1.0))
