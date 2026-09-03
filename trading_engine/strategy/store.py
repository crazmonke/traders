"""`ai_signals` 영속화. (prompt.md v2 [Step 2] 요구사항 6)

**AI 분석이 붙은 신호만 저장한다.** `up_prob`/`sideways_prob`/`risks_json` 이 전부
NOT NULL 이라, AI 없이 저장하려면 없는 확률을 지어내야 한다. 룰 엔진만으로 낸 평가는
Redis `consensus:{symbol}:{tf}` 에 남고(Step 2-a), DB 에는 들어가지 않는다.

DECIMAL 컬럼의 자릿수를 넘기면 INSERT 가 통째로 실패한다. 새벽에 거래량이 0 에 가까운
봉 다음에 급증이 오면 `volume_change_pct` 가 쉽게 6자리를 넘기므로 넣기 전에 자른다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from trading_engine.ai.analyzer import AiAnalysis
from trading_engine.database import mysql
from trading_engine.strategy.signal_engine import SignalEvaluation

log = logging.getLogger(__name__)

INSERT_SQL = (
    "INSERT INTO ai_signals ("
    "symbol, timeframe, signal_type, tech_score, ai_score, risk_score, final_score, "
    "up_prob, sideways_prob, down_prob, entry_price_global, entry_price_upbit, "
    "rsi_val, macd_val, bollinger_position, stochastic_k, stochastic_d, adx_val, cci_val, "
    "volume_change_pct, exchange_consensus_pct, data_sources_json, reasons_json, risks_json"
    ") VALUES ("
    "%s, %s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s)"
)


def _score(value: float | None) -> int | None:
    """TINYINT UNSIGNED 컬럼용. 0~100 으로 자른 정수."""
    if value is None:
        return None
    return int(round(min(max(float(value), 0.0), 100.0)))


def _decimal(value: Any, limit: float) -> float | None:
    """DECIMAL 컬럼용. None 은 그대로, 나머지는 ±limit 로 자른다."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return min(max(number, -limit), limit)


def _percent(ratio: Any, limit: float) -> float | None:
    if ratio is None:
        return None
    try:
        return _decimal(float(ratio) * 100.0, limit)
    except (TypeError, ValueError):
        return None


def data_sources_json(evaluation: SignalEvaluation, analysis: AiAnalysis) -> str:
    """`data_sources_json` — 어떤 거래소와 어떤 모델로 만들어진 신호인지.

    AI 가 스스로 낸 등급(`analysis.signal`)도 같이 남긴다. 저장되는 `signal_type` 은
    룰 엔진이 정한 값이므로(README §7 "Rule Engine 우선"), 나중에 "룰과 AI 중 무엇이
    맞았나"를 보려면 AI 의 원래 의견이 남아 있어야 한다. 이 값을 위한 컬럼은 없다.
    """
    sources = evaluation.data_sources()
    sources["ai"] = {
        "model": analysis.model,
        "signal": analysis.signal,
        "score": analysis.ai_score,
        "probability_sum": round(analysis.probability_sum, 2),
        # up_prob 등은 NOT NULL 이라 값은 그대로 저장한다. 화면이 이 플래그를 보고
        # 신뢰할 수 없는 확률을 숨긴다 — 기록은 남기되 보여주지는 않는다.
        "probabilities_reliable": analysis.is_probability_sum_valid,
        # AI 는 점수에 들어가지 않는다(2026-09-03). 이 값은 "AI 는 이렇게 봤다"는 기록이고,
        # 나중에 룰과 AI 중 무엇이 맞았는지 비교하는 데 쓴다.
        "included_in_score": False,
    }
    return json.dumps(sources, ensure_ascii=False)


def build_params(
    evaluation: SignalEvaluation, analysis: AiAnalysis
) -> tuple[Any, ...]:
    snapshot: Mapping[str, Any] = evaluation.snapshot
    return (
        evaluation.symbol,
        evaluation.timeframe,
        evaluation.signal_type,
        _score(evaluation.tech.score),
        _score(analysis.ai_score),
        _score(evaluation.risk.score),
        _score(evaluation.final_score),
        _decimal(analysis.up_prob, 999.99),
        _decimal(analysis.sideways_prob, 999.99),
        _decimal(analysis.down_prob, 999.99),
        _decimal(snapshot.get("price"), 9_999_999_999.0),
        _decimal(snapshot.get("upbit_price"), 9_999_999_999.0),
        _decimal(snapshot.get("rsi"), 9999.99),
        _decimal(snapshot.get("macd"), 99_999_999.0),
        snapshot.get("bollinger_position"),
        _decimal(snapshot.get("stochastic_k"), 9999.99),
        _decimal(snapshot.get("stochastic_d"), 9999.99),
        _decimal(snapshot.get("adx"), 9999.99),
        _decimal(snapshot.get("cci"), 999_999.99),
        _percent(snapshot.get("volume_change_rate"), 999_999.99),
        _decimal(evaluation.consensus.pct, 999.99),
        data_sources_json(evaluation, analysis),
        json.dumps(list(analysis.reasons), ensure_ascii=False),
        json.dumps(list(analysis.risks), ensure_ascii=False),
    )


async def save_signal(evaluation: SignalEvaluation, analysis: AiAnalysis) -> int | None:
    """`ai_signals` 에 한 행 넣고 id 를 돌려준다. 실패하면 None."""
    if evaluation.snapshot.get("price") is None:
        # entry_price_global 이 NOT NULL 이다. 가격을 못 낸 신호는 저장하지 않는다.
        log.warning("글로벌 가격이 없어 신호를 저장하지 않는다 (%s)", evaluation.symbol)
        return None
    try:
        return await mysql.execute(INSERT_SQL, build_params(evaluation, analysis))
    except Exception:
        log.exception("ai_signals 저장 실패 (%s)", evaluation.symbol)
        return None
