"""안정성 판정 — "이 효과가 시기를 넘어 유지되는가".

### 왜 이 파일이 필요한가

두 번 같은 함정에 빠졌다.

- **Step 16 (배점표)**: 최근 14일에서 RSI 과매도가 가장 나빴는데, 56~70일 전에는 가장
  좋았다. 한 구간에 맞춰 배점을 고쳤다면 다음 국면에서 정반대로 작동했을 것이다.
- **Step 14 (파생 데이터)**: "신규 롱 + 펀딩 양수" 조합이 두 구간에서 +0.36% / +1.15%
  였는데 나머지 한 구간에서 -0.234% 였다. 두 구간만 봤다면 채택했을 것이다.

**세 구간으로는 "안정적인가"를 판정할 수 없다.** 부호가 3번 다 같을 확률은 동전
던지기로도 25% 다. 그래서 구간을 늘리고, 판정을 눈대중이 아니라 **부호 검정**으로 한다.

### 판정 규칙 (눈대중을 없애기 위해 명시한다)

    표본 8구간 미만        → 판단 보류
    부호 검정 p >= 0.05    → 불안정 (부호가 일관되지 않는다)
    |중앙값| < 최소 효과    → 일관되나 효과가 기준 미만
    그 외                  → 안정

최소 효과의 기본값은 **왕복 거래 비용**이다. 그보다 작은 개선은 실거래에서 사라진다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from math import comb

# 왕복 거래 비용(%). `backtest.costs.REFERENCE` 기준 (수수료 0.06% + 슬리피지 0.03%) × 2.
ROUND_TRIP_COST_PCT = 0.18

# 이보다 구간이 적으면 판정하지 않는다. 부호가 우연히 맞을 확률이 너무 크다
# (동전 던지기로 3연속 같은 부호가 나올 확률이 25% 다).
MIN_WINDOWS = 8

SIGNIFICANCE = 0.05

VERDICT_STABLE = "안정"
VERDICT_UNSTABLE = "불안정"
VERDICT_TOO_SMALL = "효과 미달"
VERDICT_INSUFFICIENT = "판단 보류"


@dataclass(frozen=True)
class SignTest:
    """부호 검정 결과. 0 은 무승부로 보고 표본에서 뺀다."""

    positive: int
    negative: int
    ties: int
    p_value: float

    @property
    def decided(self) -> int:
        return self.positive + self.negative


def sign_test(values: list[float]) -> SignTest:
    """양수·음수 개수로 "부호가 한쪽으로 쏠렸는가"를 검정한다 (양측).

    평균을 쓰지 않는 이유: 한 구간의 극단값 하나가 평균을 통째로 뒤집을 수 있다.
    실제로 Step 14 에서 한 구간의 +1.15% 가 그런 역할을 할 뻔했다. 부호만 세면
    그 구간도 다른 구간과 똑같이 한 표다.
    """
    positive = sum(1 for v in values if v > 0)
    negative = sum(1 for v in values if v < 0)
    ties = len(values) - positive - negative
    n = positive + negative
    if n == 0:
        return SignTest(positive, negative, ties, 1.0)

    extreme = max(positive, negative)
    tail = sum(comb(n, i) for i in range(extreme, n + 1)) / (2**n)

    return SignTest(positive, negative, ties, min(1.0, 2 * tail))


@dataclass(frozen=True)
class Stability:
    verdict: str
    median: float
    test: SignTest
    detail: str

    @property
    def is_stable(self) -> bool:
        return self.verdict == VERDICT_STABLE


def assess(values: list[float], min_effect: float = ROUND_TRIP_COST_PCT) -> Stability:
    """구간별 값들이 "믿을 만한 효과"인지 판정한다.

    `values` 는 구간마다 하나씩 나온 값이다 — 변형과 기준의 차이(개선폭)를 넣는 것이
    보통이고, 절대 수익률을 넣어도 된다.
    """
    if len(values) < MIN_WINDOWS:
        return Stability(
            VERDICT_INSUFFICIENT,
            statistics.median(values) if values else 0.0,
            sign_test(values),
            f"구간이 {len(values)}개다. {MIN_WINDOWS}개 이상이어야 판정한다",
        )

    test = sign_test(values)
    median = statistics.median(values)

    if test.p_value >= SIGNIFICANCE:
        return Stability(
            VERDICT_UNSTABLE,
            median,
            test,
            f"양수 {test.positive} / 음수 {test.negative} (p={test.p_value:.3f}) — "
            "시기에 따라 부호가 바뀐다",
        )

    if abs(median) < min_effect:
        return Stability(
            VERDICT_TOO_SMALL,
            median,
            test,
            f"부호는 일관되나(p={test.p_value:.3f}) 중앙값 {median:+.3f}% 가 "
            f"기준 {min_effect}% 미만이다 — 실거래에서 비용에 먹힌다",
        )

    return Stability(
        VERDICT_STABLE,
        median,
        test,
        f"양수 {test.positive} / 음수 {test.negative} (p={test.p_value:.3f}), "
        f"중앙값 {median:+.3f}%",
    )
