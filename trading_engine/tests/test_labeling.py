"""정답 정의(삼중 배리어) 단위 테스트.

**백테스트와 적중률이 같은 것을 재는지**가 이 파일의 핵심이다. 두 곳이 갈라지면
"백테스트는 좋아졌는데 적중률은 그대로"인 상황에서 무엇을 믿을지 알 수 없게 된다.
"""

import pytest

from trading_engine.backtest.engine import BacktestParams
from trading_engine.backtest.costs import GLOBAL_CONSENSUS
from trading_engine.strategy import labeling
from trading_engine.strategy.labeling import (
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    EXIT_TIME_LIMIT,
    HORIZONS,
    barriers,
    label,
)

MIN = 60_000


def bar(minute, high, low, close=None):
    return {
        "ts": minute * MIN,
        "open": low,
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
        "volume": 1.0,
    }


# --- 백테스트와의 일치 ---------------------------------------------------------


def test_backtest_uses_the_same_barriers():
    """상수가 갈라지면 두 지표가 서로 다른 전략을 재게 된다."""
    params = BacktestParams(symbol="BTC", reference_exchange=GLOBAL_CONSENSUS)

    assert params.take_profit_pct == labeling.TAKE_PROFIT_PCT
    assert params.stop_loss_pct == labeling.STOP_LOSS_PCT


def test_horizons_match_the_database_enum():
    """`ai_signal_results.horizon` ENUM(마이그레이션 005)과 같아야 한다."""
    assert list(HORIZONS) == ["5m", "15m", "1h", "4h", "1d"]


# --- 배리어 방향 ---------------------------------------------------------------


def test_sell_barriers_are_flipped():
    """매도 신호는 가격이 내려가야 이익이다."""
    take, stop = barriers(100.0, "BUY")
    assert take == pytest.approx(105.0) and stop == pytest.approx(97.5)

    take, stop = barriers(100.0, "SELL")
    assert take == pytest.approx(95.0) and stop == pytest.approx(102.5)


# --- 판정 ---------------------------------------------------------------------


def test_take_profit_when_price_reaches_the_target():
    result = label(100.0, "BUY", [bar(0, 101, 99), bar(5, 106, 100)], "1h")

    assert result.exit_reason == EXIT_TAKE_PROFIT
    assert result.return_pct == pytest.approx(5.0)
    assert result.is_accurate is True


def test_stop_loss_when_price_breaks_the_floor():
    result = label(100.0, "BUY", [bar(0, 101, 99), bar(5, 100, 97)], "1h")

    assert result.exit_reason == EXIT_STOP_LOSS
    assert result.return_pct == pytest.approx(-2.5)
    assert result.is_accurate is False


def test_stop_loss_wins_when_both_are_touched_in_one_bar():
    """OHLC 로는 순서를 알 수 없다. 유리한 쪽을 가정하면 통계가 부풀려진다."""
    result = label(100.0, "BUY", [bar(0, 110, 90)], "1h")

    assert result.exit_reason == EXIT_STOP_LOSS


def test_sell_signal_profits_when_price_falls():
    result = label(100.0, "SELL", [bar(0, 100, 94)], "1h")

    assert result.exit_reason == EXIT_TAKE_PROFIT
    assert result.return_pct == pytest.approx(5.0)  # 방향 기준이라 양수
    assert result.is_accurate is True


def test_time_limit_is_labeled_by_sign_not_left_unresolved():
    """미결로 두면 '안 끝난 거래'가 통계에서 빠져 승률이 왜곡된다."""
    up = label(100.0, "BUY", [bar(0, 101, 99, close=101)], "1h")
    down = label(100.0, "BUY", [bar(0, 100, 99, close=99)], "1h")

    assert up.exit_reason == EXIT_TIME_LIMIT and up.is_accurate is True
    assert down.exit_reason == EXIT_TIME_LIMIT and down.is_accurate is False


# --- 시간 제한 -----------------------------------------------------------------


def test_bars_past_the_horizon_are_ignored():
    """5분 제한인데 30분 뒤 익절에 닿은 것을 성공으로 세면 안 된다."""
    bars = [bar(0, 101, 99, close=100), bar(30, 110, 100)]

    short = label(100.0, "BUY", bars, "5m")
    long = label(100.0, "BUY", bars, "1h")

    assert short.exit_reason == EXIT_TIME_LIMIT
    assert long.exit_reason == EXIT_TAKE_PROFIT


def test_longer_horizon_sees_more():
    """배리어까지 걸린 시간이 곧 '얼마나 들고 있어야 하는가'다."""
    bars = [bar(m, 101, 99, close=100) for m in range(0, 120, 5)] + [bar(120, 110, 100)]

    assert label(100.0, "BUY", bars, "1h").exit_reason == EXIT_TIME_LIMIT
    assert label(100.0, "BUY", bars, "4h").exit_reason == EXIT_TAKE_PROFIT


def test_best_and_worst_are_recorded_for_recalibration():
    """배리어에 얼마나 근접했는지가 남아야 폭을 다시 잡을 근거가 된다."""
    result = label(100.0, "BUY", [bar(0, 104, 98), bar(5, 103, 97.6)], "1h")

    assert result.best_price == pytest.approx(104.0)
    assert result.worst_price == pytest.approx(97.6)


# --- 평가하지 않는 것 -----------------------------------------------------------


def test_hold_is_not_labeled():
    """진입하지 않았으므로 맞고 틀리고가 없다."""
    assert label(100.0, "HOLD", [bar(0, 110, 90)], "1h") is None


def test_missing_bars_or_price_is_not_labeled():
    assert label(100.0, "BUY", [], "1h") is None
    assert label(0.0, "BUY", [bar(0, 110, 90)], "1h") is None


def test_unknown_horizon_raises():
    """조용히 넘어가면 잘못된 기간으로 잰 통계가 섞인다."""
    with pytest.raises(ValueError):
        label(100.0, "BUY", [bar(0, 101, 99)], "3h")
