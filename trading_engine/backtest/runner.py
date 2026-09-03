"""백테스트 실행 파이프라인 — 수집 → 정렬 → 재생 → 저장.

Step 4-a 는 신호 검증용(`GLOBAL_CONSENSUS`)만 노출한다. 업비트 실전용은 기준 가격
시계열과 비용 모델만 바꾸면 되도록 `run_backtest` 를 열어 뒀고, 실제 진입점은
Step 4-b 가 붙인다.
"""

from __future__ import annotations

import logging
from typing import Any

from trading_engine.backtest import costs as costs_mod
from trading_engine.backtest import data as data_mod
from trading_engine.backtest import store as backtest_store
from trading_engine.backtest.costs import GLOBAL_CONSENSUS
from trading_engine.backtest.engine import Backtester, BacktestParams, BacktestResult
from trading_engine.config import settings
from trading_engine.market.exchange_feed import CANDLE_TIMEFRAME
from trading_engine.market.exchange_registry import resolve_specs
from trading_engine.strategy.consensus import MIN_VALID_EXCHANGES

log = logging.getLogger(__name__)

# 커버리지가 가장 넓은 거래소 대비 이 비율에 못 미치면 그 거래소를 뺀다.
#
# 거래소마다 한 번에 주는 봉 수가 다르고(실측: binance 576, okx 300, upbit 200),
# 업비트처럼 `since` 를 무시하고 최근 N 개만 주는 곳도 있다. 전 거래소 교집합만 쓰면
# **30일치를 요청해도 가장 짧은 거래소에 맞춰 조용히 몇 시간으로 줄어든다.**
# 짧은 쪽을 빼고 구간을 지키되, §3.2 의 최소 표본(3개)은 유지한다.
MIN_COVERAGE_RATIO = 0.5


async def collect(
    symbol: str,
    since_ms: int,
    until_ms: int,
    exchanges: list[str] | None = None,
    timeframe: str = CANDLE_TIMEFRAME,
) -> dict[str, list[dict[str, Any]]]:
    """거래소별 캔들을 모아 공통 타임스탬프로 맞춘다."""
    specs = resolve_specs(exchanges or settings.exchanges)
    collected: dict[str, list[dict[str, Any]]] = {}

    for spec in specs:
        try:
            candles = await data_mod.fetch_candles(
                spec, symbol, timeframe, since_ms, until_ms
            )
        except Exception:
            # 한 거래소가 빠져도 나머지로 합의를 낼 수 있다. 3개 미만이면
            # 신호 자체가 HOLD 로 강등되므로(§3.2) 결과에 그대로 드러난다.
            log.exception("캔들 수집 실패 - 이 거래소는 빼고 진행한다 (%s)", spec.code)
            continue
        if candles:
            collected[spec.code] = candles
            log.info("%s %s %d봉", spec.code, spec.symbol(symbol), len(candles))

    return data_mod.align(drop_short_coverage(collected))


def drop_short_coverage(
    per_exchange: dict[str, list[dict[str, Any]]],
    min_ratio: float = MIN_COVERAGE_RATIO,
) -> dict[str, list[dict[str, Any]]]:
    """커버리지가 크게 모자란 거래소를 뺀다. 3개 미만이 되면 빼지 않는다.

    3개 미만이면 어차피 합의가 성립하지 않아(§3.2) 전 구간이 HOLD 로 강등된다.
    그럴 바에는 구간이 짧더라도 표본을 유지하는 편이 낫고, 짧아진 사실은 로그에 남는다.
    """
    if len(per_exchange) <= MIN_VALID_EXCHANGES:
        return per_exchange

    longest = max(len(candles) for candles in per_exchange.values())
    keep = {
        code: candles
        for code, candles in per_exchange.items()
        if len(candles) >= longest * min_ratio
    }
    if len(keep) < MIN_VALID_EXCHANGES:
        return per_exchange

    for code in per_exchange.keys() - keep.keys():
        log.warning(
            "%s 는 커버리지가 짧아 백테스트에서 제외한다 (%d봉 < %d봉의 %.0f%%)",
            code,
            len(per_exchange[code]),
            longest,
            min_ratio * 100,
        )

    return keep


def run_backtest(
    per_exchange: dict[str, list[dict[str, Any]]],
    params: BacktestParams,
) -> BacktestResult:
    """정렬된 캔들로 재생한다. 네트워크·DB 를 건드리지 않는다."""
    if params.reference_exchange == GLOBAL_CONSENSUS:
        price_series = data_mod.global_price_series(per_exchange)
    else:
        price_series = data_mod.single_exchange_series(
            per_exchange, params.reference_exchange
        )

    cost_model = costs_mod.for_reference_exchange(params.reference_exchange)

    return Backtester(params, cost_model).run(per_exchange, price_series)


async def run_signal_validation(
    symbol: str,
    since_ms: int,
    until_ms: int,
    *,
    user_id: int | None = None,
    exchanges: list[str] | None = None,
    timeframe: str = CANDLE_TIMEFRAME,
    initial_capital: float = 1_000_000.0,
) -> BacktestResult:
    """신호 검증용 백테스트 (`reference_exchange='GLOBAL_CONSENSUS'`).

    **이 숫자로 실전 수익을 추정하면 안 된다.** 참고용 평균 수수료를 쓰기 때문이다.
    실전 추정은 업비트 실전용(Step 4-b)이 따로 낸다.

    `user_id` 를 주면 `backtest_logs` 에 저장한다. 주지 않으면 계산만 한다 —
    같은 입력이면 결과가 재현되므로 저장 없이 돌려 보는 것이 안전하다.
    """
    per_exchange = await collect(symbol, since_ms, until_ms, exchanges, timeframe)
    params = BacktestParams(
        symbol=symbol,
        reference_exchange=GLOBAL_CONSENSUS,
        initial_capital=initial_capital,
    )
    result = run_backtest(per_exchange, params)

    log.info(
        "백테스트 %s [%s] 거래 %d건 수익률 %.2f%% 승률 %.1f%% MDD %.2f%%",
        symbol,
        GLOBAL_CONSENSUS,
        result.metrics.total_trades,
        result.metrics.total_return_pct,
        result.metrics.win_rate,
        result.metrics.mdd,
    )

    if user_id is not None:
        await backtest_store.save(result, user_id)

    return result
