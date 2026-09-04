"""워크포워드 검증 (Step 17) 단위 테스트.

**이 파일이 지키는 것은 "우연을 실력으로 착각하지 않는다"** 다. 같은 함정에 두 번
빠졌기 때문에(Step 16 배점표, Step 14 파생 데이터) 판정 규칙 자체를 고정한다.

네트워크·거래소를 부르지 않는다.
"""

import pytest

from trading_engine.validation import stability
from trading_engine.validation.stability import (
    MIN_WINDOWS,
    VERDICT_INSUFFICIENT,
    VERDICT_STABLE,
    VERDICT_TOO_SMALL,
    VERDICT_UNSTABLE,
    assess,
    sign_test,
)
from trading_engine.validation.walkforward import Cell, parse_overrides, window_edges
from trading_engine.validation.windows import DAY_MS, Window, generate, utc_midnight


# --- 부호 검정 -----------------------------------------------------------------


def test_three_windows_agreeing_is_not_evidence():
    """동전 던지기로도 3연속 같은 부호가 25% 다.

    Step 16·14 에서 3구간으로 판단할 뻔했다. 그 표본으로는 아무것도 말할 수 없다.
    """
    assert sign_test([1.0, 1.0, 1.0]).p_value == pytest.approx(0.25)


def test_eleven_of_twelve_is_evidence():
    assert sign_test([1.0] * 11 + [-1.0]).p_value < 0.01


def test_split_evidence_is_worthless():
    assert sign_test([1.0, -1.0] * 6).p_value == pytest.approx(1.0)


def test_zeros_are_ties_not_wins():
    """0 을 양수로 세면 "차이가 없다"가 "개선됐다"로 둔갑한다."""
    result = sign_test([0.0, 0.0, 1.0, -1.0])

    assert result.ties == 2
    assert result.decided == 2


def test_empty_input_is_not_significant():
    assert sign_test([]).p_value == 1.0


# --- 판정 ----------------------------------------------------------------------


def test_too_few_windows_is_withheld_not_decided():
    """Step 14 의 3구간 결과(+0.36 / -0.23 / +1.15)로는 판정하지 않는다."""
    result = assess([0.36, -0.23, 1.15])

    assert result.verdict == VERDICT_INSUFFICIENT
    assert str(MIN_WINDOWS) in result.detail


def test_flipping_sign_is_unstable():
    result = assess([0.5, -0.4] * 6)

    assert result.verdict == VERDICT_UNSTABLE
    assert "부호가 바뀐다" in result.detail


def test_consistent_but_tiny_effect_is_rejected():
    """비용보다 작은 개선은 실거래에서 사라진다. 통계적 유의성만으로 채택하지 않는다."""
    result = assess([0.05] * 11 + [-0.01])

    assert result.verdict == VERDICT_TOO_SMALL
    assert result.test.p_value < 0.05  # 유의하기는 하다


def test_consistent_and_large_effect_is_stable():
    result = assess([0.3] * 11 + [-0.1])

    assert result.verdict == VERDICT_STABLE
    assert result.is_stable


def test_median_not_mean_so_one_window_cannot_carry_it():
    """Step 14 에서 한 구간의 +1.15% 가 평균을 통째로 끌어올릴 뻔했다."""
    values = [-0.05] * 6 + [0.02] * 5 + [50.0]

    result = assess(values)

    assert result.median < 0.18
    assert result.verdict != VERDICT_STABLE


def test_min_effect_defaults_to_round_trip_cost():
    """기준이 왕복 비용인 것은 우연이 아니다 — 그보다 작으면 남는 게 없다."""
    assert stability.ROUND_TRIP_COST_PCT == pytest.approx(0.18)


# --- 구간 ----------------------------------------------------------------------


def test_windows_do_not_overlap():
    """겹친 구간은 같은 봉을 여러 번 세는 것이라 독립된 표가 아니다."""
    windows = generate(count=6, now_ms=1788480000000)

    for older, newer in zip(windows[1:], windows[:-1]):
        assert older.until_ms == newer.since_ms


def test_windows_are_anchored_to_utc_midnight():
    """실행할 때마다 경계가 밀리면 캐시가 매번 무효가 된다."""
    noon = 1788480000000 + 12 * 3_600_000

    windows = generate(count=3, now_ms=noon)

    assert windows[0].until_ms == utc_midnight(noon)
    assert all(w.since_ms % DAY_MS == 0 for w in windows)


def test_the_unfinished_current_day_is_excluded():
    """아직 안 끝난 구간을 넣으면 최근 구간만 표본이 모자란다."""
    now = 1788480000000 + 5 * 3_600_000

    assert generate(count=1, now_ms=now)[0].until_ms <= now - 5 * 3_600_000


def test_windows_go_backwards_in_time():
    windows = generate(count=4, now_ms=1788480000000)

    assert [w.label for w in windows] == ["#1", "#2", "#3", "#4"]
    assert windows[0].since_ms > windows[-1].since_ms


# --- 집계 ----------------------------------------------------------------------


def cell(index, trades, gross, symbol="BTC"):
    return Cell(
        symbol=symbol,
        window=Window(index=index, since_ms=0, until_ms=DAY_MS),
        trades=trades,
        net_pct=gross - trades * stability.ROUND_TRIP_COST_PCT,
        gross_pct=gross,
    )


def test_edge_is_per_trade_not_total():
    """총수익률은 '거래를 몇 번 했는가'에 지배된다.

    Step 16 에서 배리어를 넓힐수록 총수익이 좋아졌는데, 원인은 신호 개선이 아니라
    거래 수 감소였다. 거래당으로 재야 그 착각을 피한다.
    """
    few = cell(0, trades=10, gross=5.0)  # 거래당 0.5%
    many = cell(0, trades=100, gross=20.0)  # 거래당 0.2% (총수익은 4배)

    assert few.per_trade_gross > many.per_trade_gross
    assert few.edge > many.edge


def test_zero_trades_does_not_divide_by_zero():
    assert cell(0, trades=0, gross=0.0).per_trade_gross == 0.0


def test_window_edges_average_across_symbols():
    cells = [cell(0, 10, 5.0, "BTC"), cell(0, 10, 1.0, "ETH"), cell(1, 10, 3.0, "BTC")]

    edges = window_edges(cells)

    assert set(edges) == {0, 1}
    # (0.5 + 0.1)/2 - 0.18
    assert edges[0] == pytest.approx(0.3 - 0.18)


# --- CLI 인자 ------------------------------------------------------------------


def test_overrides_are_checked_against_the_dataclass():
    """오타를 조용히 무시하면 '바꿨는데 아무 일도 안 일어나는' 검증이 된다."""
    assert parse_overrides("take_profit_pct=3.0") == {"take_profit_pct": 3.0}

    with pytest.raises(SystemExit):
        parse_overrides("take_profit=3.0")


def test_empty_overrides_are_allowed():
    assert parse_overrides("") == {}
