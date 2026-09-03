"""S_Risk — 위험 점수. prompt.md v2 §3.3 의 마지막 항목.

    "S_Risk: 변동성(ATR) 과열 여부 및 Consensus 표본 수 부족 시 감점"

**높을수록 안전하다.** Final Score 가 가중합이라 다른 항목과 방향이 같아야 하기
때문이다. 100 에서 시작해 깎는다.

prompt.md 에는 구체적인 감점 폭이 없다. 아래 숫자는 이 프로젝트의 초기 캘리브레이션이며
근거는 다음과 같다. Step 7 로 적중률이 쌓이면 그 데이터로 다시 잡아야 한다.

- ATR 은 절대값이 아니라 **현재가 대비 비율(ATR%)** 로 본다. BTC 의 ATR 500달러와
  DOGE 의 ATR 0.005달러를 같은 자로 재려면 이 방법밖에 없다.
- 5분봉 ATR 이 가격의 1.5%면 한 봉에 1.5% 씩 움직인다는 뜻이다. 진입가 대비
  ±0.2% 로 적중을 판정하는(Step 7 DoD) 우리 기준에서는 이미 노이즈가 신호보다 크다.
  그래서 1.5%부터 깎기 시작한다.
- 표본 감점은 §3.2 4항(3개 미만이면 HOLD 강등)과 별개다. 강등은 등급을 막고,
  이 감점은 3~4개로 겨우 성립한 합의의 점수를 낮춘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FULL_SCORE = 100.0

# (ATR% 하한, 감점). 위에서부터 처음 걸리는 구간 하나만 적용한다.
ATR_PENALTIES: tuple[tuple[float, float], ...] = (
    (0.05, 40.0),  # 한 봉에 5% — 스캘핑 신호를 낼 상태가 아니다
    (0.03, 25.0),
    (0.015, 10.0),
)
# ATR 을 아직 못 내는 상태(캔들 워밍업)도 "안전하다"고 말할 수 없다.
ATR_UNKNOWN_PENALTY = 10.0

# 유효 거래소 수별 감점. 5개(전 거래소)면 감점 없음.
SAMPLE_PENALTIES: dict[int, float] = {0: 60.0, 1: 60.0, 2: 40.0, 3: 20.0, 4: 10.0}


@dataclass(frozen=True)
class RiskScore:
    score: float
    atr_pct: float | None
    penalties: tuple[tuple[str, float], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "atr_pct": None if self.atr_pct is None else round(self.atr_pct, 5),
            "penalties": [
                {"key": key, "points": -round(points, 2)} for key, points in self.penalties
            ],
        }


def atr_pct(atr: float | None, close: float | None) -> float | None:
    """ATR 을 현재가 대비 비율로. 심볼 간 비교가 가능해진다."""
    if atr is None or close is None or close <= 0:
        return None
    return abs(atr) / close


def compute(
    atr: float | None, close: float | None, valid_exchange_count: int
) -> RiskScore:
    ratio = atr_pct(atr, close)
    penalties: list[tuple[str, float]] = []

    if ratio is None:
        penalties.append(("atr_unknown", ATR_UNKNOWN_PENALTY))
    else:
        for threshold, points in ATR_PENALTIES:
            if ratio >= threshold:
                penalties.append((f"atr_over_{threshold:g}", points))
                break

    sample_penalty = SAMPLE_PENALTIES.get(valid_exchange_count, 0.0)
    if sample_penalty:
        penalties.append((f"sample_{valid_exchange_count}", sample_penalty))

    total = FULL_SCORE - sum(points for _, points in penalties)
    return RiskScore(
        score=min(max(total, 0.0), FULL_SCORE),
        atr_pct=ratio,
        penalties=tuple(penalties),
    )
