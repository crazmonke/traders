"""OpenAI 에 보낼 프롬프트와 응답 스키마. prompt.md v2 §4 그대로.

**원시 데이터를 보내지 않는다.** Python 이 먼저 다중 거래소 지표를 계산·정규화한 값만
보낸다 (README §5). 그래서 이 모듈은 숫자를 만들지 않고 이미 계산된 스냅샷을 §4 의
필드 이름으로 옮기기만 한다.

`strategy` 를 import 하지 않는다. 필요한 값을 인자로 받아 역방향 의존(ai → strategy)을
만들지 않기 위해서다. 조립은 `strategy/signal_pipeline.py` 가 한다.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

SIGNAL_ENUM = ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")

# 앞 문단은 prompt.md §4 그대로다. 뒤 문단은 실측(2026-09-03) 후 덧붙였다:
# 스키마에 담을 수 없는 제약(개수·단위)을 말로 하지 않으면 모델이 지키지 않는다.
# gpt-5-mini 는 확률을 0.73/0.16/0.11 처럼 0~1 로 주고 reasons 를 7개까지 냈다.
SYSTEM_PROMPT = (
    "You are an expert quantitative crypto trader. Analyze the pre-calculated, "
    "multi-exchange-aggregated technical indicators for {symbol}. This data is a "
    "volume-weighted composite across multiple exchanges (see data_sources), not from "
    "a single exchange. Do NOT fabricate raw price data. Evaluate short-term (5m, 15m) "
    "probability and return strict JSON format.\n"
    "Constraints the JSON schema cannot express - follow them exactly:\n"
    "- probabilities.up/sideways/down are PERCENTAGES in 0-100 that sum to 100 "
    "(e.g. 61.0 / 27.0 / 12.0), NOT fractions of 1.\n"
    "- ai_score is an integer 0-100, where 100 is maximally bullish and 0 maximally bearish.\n"
    "- reasons: 2 to 5 items, most important first.\n"
    "- risks: 1 to 3 items."
)

# prompt.md §4 의 스키마에서 `minimum`/`maximum`/`minItems`/`maxItems` 를 뺐다.
# OpenAI Structured Output 의 strict 모드가 지원하지 않는 키워드라, 넣으면 요청 자체가
# 거절된다. 범위와 개수는 우리가 `analyzer.py` 에서 검사한다 — 어차피 모델 응답은
# 스키마를 통과해도 값이 이상할 수 있으므로 검사는 필요하다.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": list(SIGNAL_ENUM)},
        "ai_score": {"type": "integer"},
        "probabilities": {
            "type": "object",
            "properties": {
                "up": {"type": "number"},
                "sideways": {"type": "number"},
                "down": {"type": "number"},
            },
            "required": ["up", "sideways", "down"],
            "additionalProperties": False,
        },
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["signal", "ai_score", "probabilities", "reasons", "risks"],
    "additionalProperties": False,
}

MACD_GOLDEN_CROSS = "GOLDEN_CROSS"
MACD_ABOVE_SIGNAL = "ABOVE_SIGNAL"
MACD_BELOW_SIGNAL = "BELOW_SIGNAL"
MACD_UNKNOWN = "UNKNOWN"


def macd_status(snapshot: Mapping[str, Any]) -> str:
    """§4 의 `macd_status` 문자열. 교차가 없으면 시그널선 위/아래만 알려준다."""
    if snapshot.get("macd_golden_cross"):
        return MACD_GOLDEN_CROSS
    macd = snapshot.get("macd")
    signal = snapshot.get("macd_signal")
    if macd is None or signal is None:
        return MACD_UNKNOWN
    return MACD_ABOVE_SIGNAL if macd > signal else MACD_BELOW_SIGNAL


def _percent(ratio: Any) -> float | None:
    """비율(0.372)을 퍼센트(37.2)로. §4 예시가 퍼센트 표기다."""
    if ratio is None:
        return None
    try:
        return round(float(ratio) * 100.0, 2)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_user_payload(
    symbol: str,
    snapshot: Mapping[str, Any],
    consensus_pct: float,
    data_sources: Sequence[str],
) -> dict[str, Any]:
    """§4 "User Data" 블록. 키 이름과 단위를 스펙에 맞춘다."""
    return {
        "symbol": symbol,
        "global_price_usd": _round(snapshot.get("price"), 8),
        "upbit_price_krw": _round(snapshot.get("upbit_price"), 8),
        "rsi_14": _round(snapshot.get("rsi")),
        "macd_status": macd_status(snapshot),
        "bollinger_position": snapshot.get("bollinger_position"),
        "stochastic_k": _round(snapshot.get("stochastic_k")),
        "stochastic_d": _round(snapshot.get("stochastic_d")),
        "adx": _round(snapshot.get("adx")),
        "cci": _round(snapshot.get("cci")),
        "volume_surge_pct": _percent(snapshot.get("volume_change_rate")),
        "orderbook_imbalance": _percent(snapshot.get("orderbook_imbalance")),
        "ma_trend": str(snapshot.get("ma_trend", "unknown")).upper(),
        "exchange_consensus_pct": round(float(consensus_pct), 2),
        "data_sources": list(data_sources),
    }
