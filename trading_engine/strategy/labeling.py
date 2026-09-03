"""신호의 정답 정의 — 삼중 배리어. **백테스트와 적중률이 공유하는 단 하나의 기준.**

### 왜 이 파일이 있는가

이전에는 두 곳이 서로 다른 것을 재고 있었다:

    백테스트    익절 +5% / 손절 -2.5% 중 어디에 먼저 닿았나 (삼중 배리어)
    적중률      5분 뒤 종가가 진입가 대비 ±0.2% 를 넘었나

**같은 신호가 백테스트에서는 성공, 적중률에서는 실패로 기록될 수 있었다.** 그러면
"백테스트가 좋아졌는데 적중률은 그대로"인 상황에서 무엇을 믿어야 할지 알 수 없다.
그래서 정답 정의를 이 파일 하나로 모으고, 양쪽이 여기서 가져다 쓴다.

### 왜 ±0.2% 가 잘못된 정의인가

5분 뒤 ±0.2% 를 맞추는 문제는 **왕복 수수료(신호검증용 0.18%, 업비트 실전용 0.20%)를
빼면 기댓값이 0 에 수렴한다.** 맞춰도 남는 게 없는 정답을 쫓는 셈이다.
실제 매매는 "익절선·손절선을 걸어 두고 어디에 먼저 닿는지 기다리는" 것이므로,
정답 정의도 그 모양이어야 백테스트가 현실을 반영한다.

### 시간 제한(horizon)을 여러 개 두는 이유

배리어가 닿기까지 걸리는 시간이 곧 "얼마나 들고 있어야 하는가"다. 짧은 horizon 에서
대부분 `TIME_LIMIT` 으로 끝난다면 그 신호는 단기 매매용이 아니라는 뜻이고, 그 사실이
데이터로 드러나야 한다. 그래서 짧은 것도 남기고 긴 것을 더한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# 익절·손절 폭. **백테스트 기본값과 같은 값이어야 한다** —
# `backtest.engine.BacktestParams` 가 이 상수를 가져다 쓴다.
# 근거는 ROADMAP "전략 재보정 1차" (손익비 2:1, 격자 가장자리 회피).
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = 2.5

# 시간 제한. `ai_signal_results.horizon` ENUM 과 같은 값이어야 한다.
HORIZONS: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

EXIT_TAKE_PROFIT = "TAKE_PROFIT"
EXIT_STOP_LOSS = "STOP_LOSS"
EXIT_TIME_LIMIT = "TIME_LIMIT"

# 방향이 없는 신호는 평가하지 않는다. 진입하지 않았으므로 맞고 틀리고가 없다.
UNLABELED_SIGNALS = frozenset({"HOLD"})

BUY_SIGNALS = frozenset({"STRONG_BUY", "BUY"})
SELL_SIGNALS = frozenset({"STRONG_SELL", "SELL"})


@dataclass(frozen=True)
class Label:
    """한 신호를 한 시간 제한으로 평가한 결과."""

    horizon: str
    exit_reason: str
    exit_price: float
    return_pct: float
    """진입 방향 기준 손익률. 매도 신호는 가격이 내려가야 양수다."""

    is_accurate: bool
    best_price: float
    worst_price: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "exit_reason": self.exit_reason,
            "exit_price": round(self.exit_price, 8),
            "return_pct": round(self.return_pct, 4),
            "is_accurate": self.is_accurate,
            "best_price": round(self.best_price, 8),
            "worst_price": round(self.worst_price, 8),
        }


def barriers(entry_price: float, signal_type: str) -> tuple[float, float]:
    """(익절가, 손절가). 매도 신호는 가격이 내려가야 이익이므로 위아래가 뒤집힌다."""
    take = TAKE_PROFIT_PCT / 100.0
    stop = STOP_LOSS_PCT / 100.0
    if signal_type in SELL_SIGNALS:
        return entry_price * (1.0 - take), entry_price * (1.0 + stop)

    return entry_price * (1.0 + take), entry_price * (1.0 - stop)


def label(
    entry_price: float,
    signal_type: str,
    bars: Sequence[Mapping[str, Any]],
    horizon: str,
) -> Label | None:
    """진입 이후 봉들을 훑어 어느 배리어에 먼저 닿았는지 판정한다.

    `bars` 는 진입 **이후**의 봉(오래된 순)이며 시간 제한만큼만 넘겨도 되고 더 넘겨도 된다 —
    여기서 horizon 분만큼 잘라 쓴다.

    한 봉 안에서 익절가와 손절가에 모두 닿았으면 **손절이 먼저**라고 본다.
    OHLC 로는 순서를 알 수 없고, 유리한 쪽을 가정하면 통계가 부풀려진다
    (`backtest.engine` 과 같은 규칙).

    방향이 없는 신호(HOLD)나 봉이 없으면 None — 평가 대상이 아니다.
    """
    if signal_type in UNLABELED_SIGNALS or not bars or entry_price <= 0:
        return None
    if horizon not in HORIZONS:
        raise ValueError(f"모르는 horizon 이다: {horizon!r}")

    limit_ms = HORIZONS[horizon] * 60_000
    start_ts = int(bars[0]["ts"])
    window = [b for b in bars if int(b["ts"]) - start_ts < limit_ms]
    if not window:
        return None

    take_price, stop_price = barriers(entry_price, signal_type)
    is_sell = signal_type in SELL_SIGNALS
    best = max(float(b["high"]) for b in window)
    worst = min(float(b["low"]) for b in window)

    for bar in window:
        high, low = float(bar["high"]), float(bar["low"])
        hit_stop = (high >= stop_price) if is_sell else (low <= stop_price)
        hit_take = (low <= take_price) if is_sell else (high >= take_price)
        if hit_stop:
            return _label(horizon, EXIT_STOP_LOSS, stop_price, entry_price, is_sell, best, worst)
        if hit_take:
            return _label(horizon, EXIT_TAKE_PROFIT, take_price, entry_price, is_sell, best, worst)

    # 어느 배리어에도 닿지 않았다. 시간 제한 종가로 마감한다.
    return _label(
        horizon, EXIT_TIME_LIMIT, float(window[-1]["close"]), entry_price, is_sell, best, worst
    )


def _label(
    horizon: str,
    reason: str,
    exit_price: float,
    entry_price: float,
    is_sell: bool,
    best: float,
    worst: float,
) -> Label:
    move = (exit_price - entry_price) / entry_price * 100.0
    return_pct = -move if is_sell else move

    return Label(
        horizon=horizon,
        exit_reason=reason,
        exit_price=exit_price,
        return_pct=return_pct,
        # 익절은 성공, 손절은 실패. 시간 제한은 **부호로 판정**한다 — 미결로 두면
        # 통계에서 통째로 빠져 "안 끝난 거래"가 승률을 왜곡한다.
        is_accurate=return_pct > 0.0,
        best_price=best,
        worst_price=worst,
    )
