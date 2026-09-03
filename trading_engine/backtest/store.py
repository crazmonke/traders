"""백테스트 결과 영속화 — `backtest_logs`. (prompt.md v2 [Step 4] 요구사항 6)

**신호검증용과 실전용을 한 행에 합치지 않는다.** `reference_exchange` 가 다르면 다른
행이고, 조회할 때도 이 컬럼으로 갈라서 본다. 같은 전략이라도 비용 모델이 달라 숫자가
달라지므로, 두 결과를 평균 내거나 합산하면 둘 다 거짓말이 된다(요구사항 4).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from trading_engine.backtest.engine import BacktestResult
from trading_engine.database import mysql

log = logging.getLogger(__name__)

INSERT_SQL = (
    "INSERT INTO backtest_logs ("
    "user_id, symbol, reference_exchange, strategy_name, start_date, end_date, "
    "initial_capital, final_capital, total_return_pct, win_rate, avg_profit_loss_ratio, "
    "mdd, total_trades, params_json"
    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

# 기본 전략 이름. `strategy_name` VARCHAR(50).
STRATEGY_NAME = "rule_engine_v2"


def _date(ts_ms: int | None) -> str:
    """UTC 기준 날짜. `start_date`/`end_date` 가 DATE 라 시각은 버린다."""
    if ts_ms is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _decimal(value: float | None, limit: float) -> float | None:
    """DECIMAL 컬럼 자릿수를 넘지 않게 자른다. 넘으면 INSERT 가 통째로 실패한다."""
    if value is None:
        return None
    return min(max(float(value), -limit), limit)


def build_params(result: BacktestResult, user_id: int) -> tuple[Any, ...]:
    metrics = result.metrics
    params = {
        **result.as_dict()["params"],
        "cost_model": result.as_dict()["cost_model"],
        "bars_evaluated": result.bars_evaluated,
        "wins": metrics.wins,
        "losses": metrics.losses,
        # 거래 내역 전부를 넣으면 JSON 이 수 MB 가 된다. 요약만 남기고
        # 상세는 필요할 때 같은 입력으로 다시 돌린다(결과가 재현되므로 가능하다).
        "exit_reasons": _exit_reason_counts(result),
    }

    return (
        user_id,
        result.params.symbol,
        result.params.reference_exchange,
        STRATEGY_NAME,
        _date(result.start_ts),
        _date(result.end_ts),
        _decimal(metrics.initial_capital, 9_999_999_999_999_999.0),
        _decimal(metrics.final_capital, 9_999_999_999_999_999.0),
        _decimal(metrics.total_return_pct, 999_999.99),
        _decimal(metrics.win_rate, 999.99),
        _decimal(metrics.avg_profit_loss_ratio, 999_999.99),
        _decimal(metrics.mdd, 999.99),
        metrics.total_trades,
        json.dumps(params, ensure_ascii=False),
    )


def _exit_reason_counts(result: BacktestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in result.trades:
        counts[trade.exit_reason] = counts.get(trade.exit_reason, 0) + 1
    return counts


async def save(result: BacktestResult, user_id: int) -> int | None:
    """한 행 저장하고 id 를 돌려준다. 실패하면 None."""
    try:
        return await mysql.execute(INSERT_SQL, build_params(result, user_id))
    except Exception:
        log.exception("backtest_logs 저장 실패 (%s)", result.params.symbol)
        return None


async def recent(
    reference_exchange: str, symbol: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """기준별 최근 결과. **`reference_exchange` 조건이 필수 인자다** —
    빼먹고 조회하면 신호검증용과 실전용이 한 목록에 섞인다."""
    sql = (
        "SELECT id, symbol, reference_exchange, strategy_name, start_date, end_date, "
        "       total_return_pct, win_rate, avg_profit_loss_ratio, mdd, total_trades, "
        "       created_at "
        "  FROM backtest_logs WHERE reference_exchange = %s"
    )
    params: list[Any] = [reference_exchange]
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(limit)

    return await mysql.fetch_all(sql, tuple(params))
