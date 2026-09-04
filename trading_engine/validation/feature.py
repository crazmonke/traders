"""지표 예측력 검증 — "이 값이 결과를 가르는가"를 여러 구간에서 묻는다.

같은 질문을 네 번 했다. 매번 즉석 스크립트를 썼고, 세 번은 3구간이라 판정 근거가
약했다.

    Step 16  RSI·스토캐스틱·CCI·볼린저·ADX·거래량   → 시기마다 부호가 뒤집힘
    Step 14  펀딩비·미결제약정                      → 가격 방향을 통제하면 뒤집힘
    Step 15  김치 프리미엄                          → 이 모듈로 잰다

### 무엇을 재는가

지표 값으로 신호를 몇 구간(bucket)으로 나누고, **구간별 이후 손익 차이**가 시기를
넘어 유지되는지 본다. 판정은 `stability` 의 부호 검정이다.

**한 시기의 큰 효과는 증거가 아니다.** Step 14 에서 한 구간의 +1.15% 가 평균을 통째로
끌어올릴 뻔했다. 여기서는 구간마다 한 표씩만 센다.

### 왜 상위−하위 스프레드를 보는가

"고평가 구간이 나쁘다" 만으로는 부족하다. 시장이 전체적으로 나쁜 구간에서는 모든
버킷이 나쁘다. **같은 구간 안에서 상위와 하위가 갈리는지**가 지표의 기여다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from trading_engine.strategy import labeling
from trading_engine.validation import stability
from trading_engine.validation.windows import Window


@dataclass(frozen=True)
class Bucket:
    """지표 값의 한 구간. `low <= value < high`."""

    label: str
    low: float
    high: float

    def holds(self, value: float) -> bool:
        return self.low <= value < self.high


@dataclass
class Observation:
    """신호 하나 + 그 시점의 지표 값 + 이후 결과."""

    window: int
    symbol: str
    signal_type: str
    value: float
    forward_pct: float


@dataclass
class BucketStat:
    label: str
    count: int
    mean_pct: float
    win_rate: float


def observe(
    window: Window,
    symbol: str,
    signals: Mapping[int, Any],
    series: Sequence[Mapping[str, Any]],
    values: Mapping[int, float],
    horizon: str = "1d",
) -> list[Observation]:
    """신호마다 (지표 값, 이후 손익) 을 붙인다.

    손익은 `labeling` 의 삼중 배리어다 — 백테스트·적중률과 같은 정답 정의를 쓴다.
    지표 값이 없는 신호는 버린다(있는 척하면 그 구간 통계가 오염된다).
    """
    out: list[Observation] = []
    for index, evaluation in signals.items():
        if evaluation.signal_type in labeling.UNLABELED_SIGNALS:
            continue
        if index not in values or index + 1 >= len(series):
            continue
        label = labeling.label(
            float(series[index + 1]["open"]), evaluation.signal_type, series[index + 1 :], horizon
        )
        if label is None:
            continue
        out.append(
            Observation(
                window=window.index,
                symbol=symbol,
                signal_type=evaluation.signal_type,
                value=values[index],
                forward_pct=label.return_pct,
            )
        )
    return out


def bucket_stats(observations: Sequence[Observation], buckets: Sequence[Bucket]) -> list[BucketStat]:
    stats: list[BucketStat] = []
    for bucket in buckets:
        selected = [o for o in observations if bucket.holds(o.value)]
        if not selected:
            stats.append(BucketStat(bucket.label, 0, 0.0, 0.0))
            continue
        wins = sum(1 for o in selected if o.forward_pct > 0)
        stats.append(
            BucketStat(
                bucket.label,
                len(selected),
                statistics.mean([o.forward_pct for o in selected]),
                wins / len(selected) * 100,
            )
        )
    return stats


def spread_by_window(
    observations: Sequence[Observation], buckets: Sequence[Bucket]
) -> dict[int, float]:
    """구간별 (마지막 버킷 − 첫 버킷) 평균 손익.

    양쪽 버킷에 표본이 없는 구간은 뺀다 — 0 으로 채우면 "차이 없음"으로 세어져
    부호 검정이 희석된다.
    """
    out: dict[int, float] = {}
    windows = sorted({o.window for o in observations})
    for index in windows:
        subset = [o for o in observations if o.window == index]
        low = [o.forward_pct for o in subset if buckets[0].holds(o.value)]
        high = [o.forward_pct for o in subset if buckets[-1].holds(o.value)]
        if not low or not high:
            continue
        out[index] = statistics.mean(high) - statistics.mean(low)
    return out


def report(
    name: str,
    observations: Sequence[Observation],
    buckets: Sequence[Bucket],
    min_effect: float = stability.ROUND_TRIP_COST_PCT,
) -> stability.Stability:
    print(f"\n══════ {name} · 표본 {len(observations)}건 ══════")
    print(f"{'구간':<26}{'건수':>7}{'평균 손익':>11}{'승률':>8}")
    for stat in bucket_stats(observations, buckets):
        print(f"{stat.label:<26}{stat.count:>7}{stat.mean_pct:>10.3f}%{stat.win_rate:>7.1f}%")

    spreads = spread_by_window(observations, buckets)
    print(f"\n  구간별 스프레드 ({buckets[-1].label} − {buckets[0].label}):")
    print("  " + "  ".join(f"#{i + 1} {v:+.3f}%" for i, v in sorted(spreads.items())))

    result = stability.assess(list(spreads.values()), min_effect=min_effect)
    answer = (
        ("높을수록 좋다" if result.median > 0 else "높을수록 나쁘다")
        if result.verdict == stability.VERDICT_STABLE
        else result.verdict
    )
    print(f"\n  {name} 이 결과를 가르는가: **{answer}** — {result.detail}")

    return result
