"""추세추종 전략 (2026-09-04 채택) — 일봉 30일 이평 위면 보유, 아래면 현금.

### 왜 이 전략인가

기존 5분봉 룰 엔진은 **168일 12구간 중 11구간에서 왕복 비용(0.18%)을 넘지 못했다**
(Step 17-a, p=0.006). 원인은 신호 품질이 아니라 구조다 — 거래당 기대값 0.15% 에
연 2,600회면 **비용이 총수익의 120%** 다. 배점표(16)·파생 데이터(14)·파라미터(17)를
모두 고쳐 봤지만 그 격차는 메워지지 않았다.

거래 텀을 늘리면 비용을 **이기는 대신 무의미하게 만든다.** 주 1~2회면 연 비용이
총수익의 2~3% 다.

### 무엇을 재서 골랐는가 (일봉 3,306개, 2017-08 ~ 2026-09, 왕복 비용 반영)

기준선은 **단순 보유**다. BTC 는 9년간 18.8배가 됐으므로 그것을 못 이기면 전략이
아니라 그냥 롱이다.

    "저렴할 때 사고 비쌀 때 판다" (평균회귀)   24개 조합 전부 보유에 패배
    추세추종 (이평 위면 보유)                  30일 하나로 5개 심볼 전부 승리

**심볼별로 이평을 고르지 않았다.** 30일 하나로 고정해서 BTC·ETH·XRP·SOL·DOGE
5/5 다. 20일 4/5, 50일 4/5 로 특정 값에서만 되는 것도 아니다. 그리고 봉을 바꿔도
(1시간·4시간·12시간) **20일 이상 이평이면 5/5** 였다 — 봉 크기가 아니라 "추세를 재는
기간"이 본질이다.

### 정직하게: 증명되지는 않았다

연-심볼 44건의 기하평균은 1.15배지만 t=1.54 로 **유의하지 않다.** 검정력 계산상
이 크기의 효과는 우리 표본(9~18구간)으로는 증명될 수 없다.

일관되게 참인 것은 **MDD 개선 하나뿐이다** — 5개 심볼 전부 최대낙폭이 10~39%p
줄었다. 수익이 아니라 위험 쪽이다.

그래서 **운영에 넣되 기존 신호와 나란히 기록하고, Step 7 실적 추적이 판정하게 한다.**
`scoring_version` 이 달라 적중률 화면에서 자동으로 분리된다.

### 김치 프리미엄은 붙이지 않았다

단독으로는 가장 강한 신호였는데(30일 뒤 승률 59% → 39% 로 단조 감소), 이 전략과
결합하면 **나빠진다**(기하평균 0.82배). 김프가 높을 때는 대개 강한 상승장 중이고,
그때가 이 전략이 버는 구간이기 때문이다. 근거는 ROADMAP "Step 15" 절.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

log = logging.getLogger(__name__)

# 이평 길이(일). 20·30·50 이 모두 5/5 였고 그중 30 이 신호 빈도(주 1.3건)와
# MDD 개선(+18%p)의 균형이 가장 좋았다. **심볼별로 다르게 두지 않는다** —
# 심볼마다 고르는 순간 그건 과최적화다.
MA_DAYS = 30

# 이평을 계산할 봉. 4시간·12시간봉도 20일 이상 이평이면 같은 결과였지만,
# 일봉이 거래 수가 가장 적어(주 1.3건) 비용이 가장 낮다.
TIMEFRAME = "1d"

# 신호가 나오려면 이만큼의 봉이 필요하다. 이평 길이 + 직전 상태 판정용 1봉.
MIN_CANDLES = MA_DAYS + 1

# `ai_signals.scoring_version` 에 들어간다. 룰 엔진 신호와 **절대 섞이면 안 된다** —
# 완전히 다른 규칙으로 만들어진 다른 것이다. VARCHAR(8) 을 넘기지 않는다.
STRATEGY_VERSION = "trend1"

SIGNAL_ENTER = "STRONG_BUY"
SIGNAL_EXIT = "SELL"

# 이 전략은 방향만 본다. 확신도 눈금이 없으므로 진입/청산에 고정값을 준다.
# 룰 엔진의 0~100 점수와 **같은 의미가 아니다** — 화면에서 섞어 비교하면 안 된다.
ENTER_SCORE = 100.0
EXIT_SCORE = 0.0
NEUTRAL_SCORE = 50.0


@dataclass(frozen=True)
class TrendState:
    """한 심볼의 현재 추세 상태."""

    symbol: str
    close: float
    moving_average: float
    above: bool
    was_above: bool
    candle_ts: int
    agreement_pct: float
    """거래소 몇 %가 각자의 이평 위에 있는가. 합의 개념을 그대로 쓴다."""

    @property
    def crossed(self) -> bool:
        """오늘 상태가 어제와 달라졌는가. **달라진 날에만 신호를 낸다.**"""
        return self.above != self.was_above

    @property
    def signal_type(self) -> str:
        return SIGNAL_ENTER if self.above else SIGNAL_EXIT

    @property
    def distance_pct(self) -> float:
        """이평 대비 몇 % 위/아래인가."""
        if self.moving_average <= 0:
            return 0.0
        return (self.close / self.moving_average - 1.0) * 100.0


def moving_average(closes: Sequence[float], window: int = MA_DAYS) -> float | None:
    """마지막 `window` 개의 단순 평균. 봉이 모자라면 None — 지어내지 않는다."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def state_from(
    symbol: str,
    series: Sequence[Mapping[str, Any]],
    per_exchange: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    window: int = MA_DAYS,
) -> TrendState | None:
    """글로벌 일봉 계열에서 현재 추세 상태를 낸다.

    **직전 봉의 상태도 함께 낸다.** 교차가 일어난 날에만 신호를 내야 하는데,
    상태만 알면 매일 같은 신호를 반복해서 내게 된다.
    """
    if len(series) < window + 1:
        return None

    closes = [float(bar["close"]) for bar in series]
    current = moving_average(closes, window)
    previous = moving_average(closes[:-1], window)
    if current is None or previous is None or current <= 0:
        return None

    return TrendState(
        symbol=symbol,
        close=closes[-1],
        moving_average=current,
        above=closes[-1] > current,
        was_above=closes[-2] > previous,
        candle_ts=int(series[-1]["ts"]),
        agreement_pct=agreement(per_exchange, window) if per_exchange else 100.0,
    )


def agreement(
    per_exchange: Mapping[str, Sequence[Mapping[str, Any]]], window: int = MA_DAYS
) -> float:
    """거래소 몇 %가 각자의 이평 위에 있는가.

    글로벌 계열 하나로만 판정하면 한 거래소의 이상치가 그대로 신호가 된다.
    §3.2 의 합의 개념을 같은 방식으로 재사용한다.
    """
    votes = []
    for candles in per_exchange.values():
        closes = [float(c["close"]) for c in candles]
        average = moving_average(closes, window)
        if average is not None:
            votes.append(closes[-1] > average)

    if not votes:
        return 0.0

    return sum(votes) / len(votes) * 100.0
