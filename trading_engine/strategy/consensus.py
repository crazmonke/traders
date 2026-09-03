"""거래소 간 합의(Exchange Consensus) — prompt.md v2 §3.2.

    1. 거래소별 Technical Score 산출 (rule_engine)
    2. 방향 분류: Score >= 60 → BUY 진영, Score <= 40 → SELL 진영, 그 외 NEUTRAL
    3. Consensus % = 다수 진영 거래소 수 / 데이터가 유효한 전체 거래소 수 × 100
    4. 유효 거래소가 3개 미만이면 Consensus 를 계산하지 않고 HOLD 로 강등 (표본 부족)
    5. Consensus < 50% 인 심볼은 AI 호출 대상에서 제외 (→ rule_engine.should_request_ai)

거래소 한 곳의 이상 호가나 저유동성 왜곡을 걸러내는 것이 목적이므로, 여기서 쓰는
"유효한 거래소"는 **점수를 낼 수 있을 만큼 캔들이 쌓인 거래소**를 말한다. 연결은
살아 있지만 워밍업 중인 거래소를 분모에 넣으면 합의율이 실제보다 낮게 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

BUY = "BUY"
SELL = "SELL"
NEUTRAL = "NEUTRAL"

BUY_THRESHOLD = 60.0
SELL_THRESHOLD = 40.0

# §3.2 4항. 이보다 적으면 합의를 말할 표본이 안 된다.
MIN_VALID_EXCHANGES = 3


def classify_direction(tech_score: float | None) -> str:
    """거래소 하나의 Technical Score 를 진영으로. (§3.2 2항)"""
    if tech_score is None:
        return NEUTRAL
    if tech_score >= BUY_THRESHOLD:
        return BUY
    if tech_score <= SELL_THRESHOLD:
        return SELL
    return NEUTRAL


@dataclass(frozen=True)
class ConsensusResult:
    symbol: str
    pct: float
    """다수 진영의 비율(0~100). 표본 부족이면 0.0 —
    `ai_signals.exchange_consensus_pct` 가 NOT NULL 이라 NULL 을 쓸 수 없다.
    "계산하지 않았다"와 "0%"의 구분은 `is_sample_sufficient` 가 한다."""

    direction: str
    valid_exchanges: tuple[str, ...]
    counts: dict[str, int]
    per_exchange: dict[str, float]
    is_sample_sufficient: bool

    @property
    def valid_count(self) -> int:
        return len(self.valid_exchanges)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "pct": round(self.pct, 2),
            "direction": self.direction,
            "valid_exchanges": list(self.valid_exchanges),
            "valid_count": self.valid_count,
            "counts": dict(self.counts),
            "per_exchange": {
                code: round(score, 2) for code, score in self.per_exchange.items()
            },
            "is_sample_sufficient": self.is_sample_sufficient,
        }


def compute(symbol: str, scores: Mapping[str, float | None]) -> ConsensusResult:
    """거래소별 Technical Score 로 합의를 낸다.

    `scores` 의 값이 None 인 거래소는 "데이터 무효"로 보고 분모에서 뺀다.
    """
    valid = {code: score for code, score in scores.items() if score is not None}
    counts = {BUY: 0, SELL: 0, NEUTRAL: 0}
    for score in valid.values():
        counts[classify_direction(score)] += 1

    total = len(valid)
    if total < MIN_VALID_EXCHANGES:
        # 계산 자체를 하지 않는다. 2개 거래소가 같은 방향이라고 100% 라 부르면
        # 표본 부족을 오히려 확신으로 뒤집어 보여주게 된다.
        return ConsensusResult(
            symbol=symbol,
            pct=0.0,
            direction=NEUTRAL,
            valid_exchanges=tuple(sorted(valid)),
            counts=counts,
            per_exchange=dict(valid),
            is_sample_sufficient=False,
        )

    majority = max(counts.values())
    leaders = [camp for camp, count in counts.items() if count == majority]
    # 동수면 다수 진영을 특정할 수 없다. 방향은 NEUTRAL(=HOLD) 로 두되,
    # 비율 자체는 그 진영 크기로 남겨 "몇 대 몇으로 갈렸는지"를 볼 수 있게 한다.
    direction = leaders[0] if len(leaders) == 1 else NEUTRAL

    return ConsensusResult(
        symbol=symbol,
        pct=majority / total * 100.0,
        direction=direction,
        valid_exchanges=tuple(sorted(valid)),
        counts=counts,
        per_exchange=dict(valid),
        is_sample_sufficient=True,
    )
