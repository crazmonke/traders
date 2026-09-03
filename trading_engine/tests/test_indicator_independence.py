"""지표 독립성 — 새 지표를 넣기 전에 반드시 통과시켜야 하는 검사.

**지표를 늘린다고 명중률이 오르지 않는다.** 서로 상관관계가 높은 지표를 겹쳐 쓰면
"여러 근거가 일치한다"는 착각만 강해지고, 그 착각이 점수로 굳어진다.

실측(BTC 1시간봉 620표본, 2026-09-03)으로 확인된 것:

    스토캐스틱 %K ↔ CCI    r = +0.844   사실상 중복
    RSI          ↔ CCI    r = +0.822   사실상 중복
    RSI          ↔ 스토캐스틱 r = +0.763
    ADX          ↔ 나머지  r = +0.07 ~ +0.50   유일하게 독립적

여기 있는 것은 계산 자체의 성질을 고정하는 테스트다. 실제 시장 데이터로 재는 것은
`ROADMAP.md` 의 "전략 자문 검토" 절차를 따른다(네트워크가 필요해 단위 테스트에 넣지 않는다).
"""

import math

import pytest

from trading_engine.indicators import calculator
from trading_engine.indicators.calculator import (
    compute_session_vwap,
    vwap_divergence_pct,
)

DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000


def bars(count, *, start_ts=DAY_MS * 10, step=HOUR_MS, price=100.0, volume=10.0):
    return [
        {
            "ts": start_ts + i * step,
            "open": price + i,
            "high": price + i + 1,
            "low": price + i - 1,
            "close": price + i,
            "volume": volume,
        }
        for i in range(count)
    ]


# --- 세션 VWAP -----------------------------------------------------------------


def test_session_vwap_uses_only_the_current_utc_day():
    """트레이딩뷰와 같은 세션 앵커드 정의. 자정 이전 봉은 섞지 않는다."""
    yesterday = bars(5, start_ts=DAY_MS * 9, price=1000.0)
    today = bars(3, start_ts=DAY_MS * 10, price=100.0)

    vwap = compute_session_vwap(yesterday + today)

    # 어제의 1000원대 가격이 섞였다면 값이 수백 단위로 튄다.
    assert vwap is not None and vwap < 200.0


def test_session_vwap_is_volume_weighted_not_a_plain_average():
    heavy = {"ts": DAY_MS * 10, "high": 100, "low": 100, "close": 100, "volume": 90.0}
    light = {"ts": DAY_MS * 10 + HOUR_MS, "high": 200, "low": 200, "close": 200, "volume": 10.0}

    # 단순 평균이면 150. 거래량 가중이면 110 에 가깝다.
    assert compute_session_vwap([heavy, light]) == pytest.approx(110.0)


def test_session_vwap_is_undefined_without_volume():
    """휴장·데이터 결측 구간에서 0으로 나누지 않는다."""
    assert compute_session_vwap([{**b, "volume": 0.0} for b in bars(5)]) is None
    assert compute_session_vwap([]) is None


def test_divergence_sign_says_which_side_of_vwap():
    assert vwap_divergence_pct(110.0, 100.0) == pytest.approx(10.0)
    assert vwap_divergence_pct(90.0, 100.0) == pytest.approx(-10.0)
    assert vwap_divergence_pct(100.0, 100.0) == pytest.approx(0.0)


def test_divergence_is_undefined_without_vwap():
    assert vwap_divergence_pct(100.0, None) is None
    assert vwap_divergence_pct(None, 100.0) is None
    assert vwap_divergence_pct(100.0, 0.0) is None


def test_vwap_is_reference_only_and_not_scored():
    """**배점표에 들어가지 않는다.** 실측에서 RSI 와 r=0.799 로 강한 상관이었다.

    참고 지표로 보여주는 것과 점수에 넣는 것은 다른 문제다. 점수에 넣으려면
    배점표 재설계(Step 16)에서 중복을 정리한 뒤에 해야 한다.
    """
    from trading_engine.strategy.rule_engine import RuleEngine

    snapshot = {"rsi": 50.0, "vwap": 100.0, "vwap_divergence": 99.0}
    without = RuleEngine().score({k: v for k, v in snapshot.items() if "vwap" not in k})

    assert RuleEngine().score(snapshot).score == without.score


# --- 상관관계 계산기 (새 지표 검증에 쓴다) --------------------------------------


def pearson(a, b):
    """두 계열의 피어슨 상관계수. |r| >= 0.8 이면 사실상 같은 지표로 본다."""
    n = len(a)
    if n < 2:
        return 0.0
    mean_a, mean_b = sum(a) / n, sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den = math.sqrt(sum((x - mean_a) ** 2 for x in a)) * math.sqrt(
        sum((y - mean_b) ** 2 for y in b)
    )

    return num / den if den else 0.0


def test_pearson_matches_known_values():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert abs(pearson([1, 2, 3, 4], [1, 1, 1, 1])) < 1e-9


def test_indicators_expose_the_fields_the_screener_needs():
    """새 지표를 넣을 때 스냅샷에 실리지 않으면 상관관계를 잴 수조차 없다."""
    snapshot = calculator.compute("BTC/USDT", bars(60)).as_dict()

    for field in ("rsi", "stochastic_k", "cci", "adx", "macd_hist", "vwap_divergence"):
        assert field in snapshot, field
