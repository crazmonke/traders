"""Step 4-a 단위 테스트 — 지표 · 비용 · 매매 시뮬레이션 · 재현성.

매매 시뮬레이션은 신호를 대본으로 주입해 **체결 규칙만** 본다. 신호 산출 자체는
`test_strategy.py` 가 이미 검증하므로, 여기서 다시 캔들을 만들어 재현하면 두 파일이
서로 다른 전략을 검증하게 된다.

재현성 테스트만은 실제 경로(캔들 → 지표 → 합의 → 등급 → 매매)를 통째로 돌린다.
"""

import pytest

from trading_engine.backtest import costs, data, metrics
from trading_engine.backtest.costs import GLOBAL_CONSENSUS
from trading_engine.backtest.engine import (
    EXIT_END_OF_DATA,
    EXIT_SIGNAL,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    WINDOW,
    Backtester,
    BacktestParams,
)
from trading_engine.backtest.metrics import Trade
from trading_engine.backtest.runner import drop_short_coverage, run_backtest
from trading_engine.strategy.signal_engine import SIGNAL_HOLD, SIGNAL_STRONG_BUY

BUCKET_MS = 5 * 60 * 1000


def bar(ts, open_, high, low, close, volume=1.0):
    return {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def flat_series(count, price=100.0, start_ts=0):
    return [bar(start_ts + i * BUCKET_MS, price, price, price, price) for i in range(count)]


def make_trade(entry_cost, exit_proceeds, reason=EXIT_SIGNAL):
    return Trade(
        symbol="BTC",
        entry_ts=0,
        exit_ts=BUCKET_MS,
        entry_price=100.0,
        exit_price=100.0,
        quantity=1.0,
        entry_cost=entry_cost,
        exit_proceeds=exit_proceeds,
        exit_reason=reason,
    )


# --- 지표 (요구사항 5) --------------------------------------------------------


def test_summarize_counts_wins_and_losses():
    trades = [make_trade(100, 120), make_trade(100, 90), make_trade(100, 110)]

    result = metrics.summarize(trades, [1000, 1020], initial_capital=1000)

    assert result.total_trades == 3
    assert result.wins == 2 and result.losses == 1
    assert result.win_rate == pytest.approx(66.67, abs=0.01)
    assert result.total_return_pct == pytest.approx(2.0)


def test_profit_loss_ratio_is_average_win_over_average_loss():
    # 이익 +20, +10 (평균 15) / 손실 -10 (평균 10) → 손익비 1.5
    trades = [make_trade(100, 120), make_trade(100, 110), make_trade(100, 90)]

    result = metrics.summarize(trades, [1000], initial_capital=1000)

    assert result.avg_profit_loss_ratio == pytest.approx(1.5)


def test_profit_loss_ratio_is_none_without_losses():
    """0 으로 채우면 "손실이 없었다"가 "손익비 0(최악)"으로 읽힌다."""
    result = metrics.summarize([make_trade(100, 120)], [1000], initial_capital=1000)

    assert result.avg_profit_loss_ratio is None
    assert result.as_dict()["avg_profit_loss_ratio"] is None


def test_mdd_is_measured_from_the_running_peak():
    # 1000 → 1200 → 900 : 최고점 1200 대비 -25%
    assert metrics.max_drawdown_pct([1000, 1200, 900, 1100]) == pytest.approx(25.0)
    assert metrics.max_drawdown_pct([1000, 1100, 1200]) == pytest.approx(0.0)
    assert metrics.max_drawdown_pct([]) == pytest.approx(0.0)


def test_empty_backtest_reports_zeroes_not_errors():
    result = metrics.summarize([], [], initial_capital=1000)

    assert result.total_trades == 0
    assert result.win_rate == 0.0
    assert result.total_return_pct == 0.0
    assert result.avg_profit_loss_ratio is None


# --- 비용 (요구사항 4) --------------------------------------------------------


def test_slippage_always_moves_against_us():
    model = costs.CostModel(fee_rate=0.0, slippage_rate=0.01, label="t")

    assert model.buy_price(100.0) == pytest.approx(101.0)
    assert model.sell_price(100.0) == pytest.approx(99.0)


def test_fees_are_charged_on_both_sides():
    model = costs.CostModel(fee_rate=0.001, slippage_rate=0.0, label="t")

    assert model.buy_cost(100.0, 1.0) == pytest.approx(100.1)
    assert model.sell_proceeds(100.0, 1.0) == pytest.approx(99.9)


def test_upbit_profile_matches_the_spec_numbers():
    """요구사항 4 — 수수료 0.05%(양방향), 슬리피지 0.05%. Step 4-b 가 쓴다."""
    assert costs.UPBIT.fee_rate == 0.0005
    assert costs.UPBIT.slippage_rate == 0.0005


def test_reference_and_upbit_models_are_not_the_same():
    """둘을 섞으면 같은 전략도 다른 숫자가 나온다. 합치지 말라는 요구사항의 근거."""
    assert costs.REFERENCE != costs.UPBIT
    assert costs.for_reference_exchange(GLOBAL_CONSENSUS) is costs.REFERENCE
    assert costs.for_reference_exchange("upbit") is costs.UPBIT


def test_unknown_reference_exchange_raises():
    """잘못된 비용으로 "수익이 났다"는 결과를 내는 것이 조용히 넘어가는 것보다 위험하다."""
    with pytest.raises(ValueError):
        costs.for_reference_exchange("binance")


# --- 매매 시뮬레이션 (요구사항 3) ---------------------------------------------


class ScriptedBacktester(Backtester):
    """신호를 대본으로 주입한다. 인덱스 → 등급."""

    def __init__(self, params, cost_model, script):
        super().__init__(params, cost_model)
        self._script = script

    def _signal_at(self, manager, signals, exchange_candles, index):
        return self._script.get(index)


NO_COST = costs.CostModel(fee_rate=0.0, slippage_rate=0.0, label="test")
PARAMS = BacktestParams(
    symbol="BTC", reference_exchange=GLOBAL_CONSENSUS, initial_capital=1000.0
)


def run_scripted(price_series, script, params=PARAMS, cost_model=NO_COST):
    candles = {"binance": price_series}
    return ScriptedBacktester(params, cost_model, script).run(candles, price_series)


def test_entry_fills_at_the_next_bar_open():
    """종가로 낸 신호를 그 봉에서 체결하면 미래를 보는 것이다."""
    series = flat_series(WINDOW + 5)
    series[WINDOW + 1] = bar(series[WINDOW + 1]["ts"], 110.0, 110.0, 110.0, 110.0)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    assert result.trades[0].entry_price == pytest.approx(110.0)
    assert result.trades[0].entry_ts == series[WINDOW + 1]["ts"]


def test_take_profit_exits_at_the_target():
    series = flat_series(WINDOW + 6)
    # 진입 다음 봉에서 +2% 까지 오른다 (익절선 +1.5%)
    series[WINDOW + 2] = bar(series[WINDOW + 2]["ts"], 100.0, 102.0, 100.0, 101.0)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    trade = result.trades[0]
    assert trade.exit_reason == EXIT_TAKE_PROFIT
    assert trade.exit_price == pytest.approx(101.5)


def test_stop_loss_exits_at_the_stop():
    series = flat_series(WINDOW + 6)
    series[WINDOW + 2] = bar(series[WINDOW + 2]["ts"], 100.0, 100.0, 98.0, 98.5)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    trade = result.trades[0]
    assert trade.exit_reason == EXIT_STOP_LOSS
    assert trade.exit_price == pytest.approx(99.0)


def test_stop_loss_wins_when_both_levels_are_touched():
    """OHLC 로는 순서를 알 수 없다. 유리한 쪽을 가정하면 백테스트가 부풀려진다."""
    series = flat_series(WINDOW + 6)
    series[WINDOW + 2] = bar(series[WINDOW + 2]["ts"], 100.0, 105.0, 95.0, 100.0)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    assert result.trades[0].exit_reason == EXIT_STOP_LOSS


def test_sell_signal_closes_the_position():
    series = flat_series(WINDOW + 8)

    result = run_scripted(
        series, {WINDOW: SIGNAL_STRONG_BUY, WINDOW + 3: "STRONG_SELL"}
    )

    assert result.trades[0].exit_reason == EXIT_SIGNAL


def test_hold_does_not_open_or_close():
    series = flat_series(WINDOW + 5)

    result = run_scripted(series, {WINDOW: SIGNAL_HOLD, WINDOW + 1: SIGNAL_HOLD})

    assert result.trades == ()
    assert result.metrics.total_trades == 0


def test_only_one_position_at_a_time():
    series = flat_series(WINDOW + 10)
    script = {index: SIGNAL_STRONG_BUY for index in range(WINDOW, WINDOW + 8)}

    result = run_scripted(series, script)

    # 매 봉 매수 신호여도 보유 중에는 다시 사지 않는다
    assert result.metrics.total_trades <= 1


def test_open_position_is_closed_when_data_ends():
    """미청산으로 남기면 그 손익이 승률·손익비에서 빠져 결과가 좋아 보인다."""
    series = flat_series(WINDOW + 5)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    assert result.trades[-1].exit_reason == EXIT_END_OF_DATA
    assert result.metrics.total_trades == 1


def test_costs_reduce_the_result():
    series = flat_series(WINDOW + 6)
    series[WINDOW + 2] = bar(series[WINDOW + 2]["ts"], 100.0, 102.0, 100.0, 101.0)
    script = {WINDOW: SIGNAL_STRONG_BUY}

    free = run_scripted(series, script, cost_model=NO_COST)
    charged = run_scripted(series, script, cost_model=costs.REFERENCE)

    assert charged.metrics.total_return_pct < free.metrics.total_return_pct


def test_equity_curve_tracks_unrealized_value():
    """거래 종료 시점만 찍으면 보유 중 낙폭이 MDD 에서 통째로 빠진다."""
    series = flat_series(WINDOW + 8)
    for index in (WINDOW + 2, WINDOW + 3):
        series[index] = bar(series[index]["ts"], 100.0, 100.0, 99.5, 99.6)

    result = run_scripted(series, {WINDOW: SIGNAL_STRONG_BUY})

    assert len(result.equity_curve) > 1
    assert result.metrics.mdd > 0.0


def test_insufficient_bars_returns_an_empty_result():
    result = run_scripted(flat_series(10), {})

    assert result.metrics.total_trades == 0
    assert result.bars_evaluated == 0
    assert result.start_ts is None


# --- 데이터 정렬·합성 (요구사항 1·2) ------------------------------------------


def test_align_keeps_only_shared_timestamps():
    """거래소마다 결측 봉이 다르다. 맞추지 않으면 같은 인덱스가 다른 시각을 가리킨다."""
    aligned = data.align(
        {
            "binance": [bar(0, 1, 1, 1, 1), bar(100, 2, 2, 2, 2), bar(200, 3, 3, 3, 3)],
            "okx": [bar(0, 1, 1, 1, 1), bar(200, 3, 3, 3, 3)],
        }
    )

    assert [c["ts"] for c in aligned["binance"]] == [0, 200]
    assert [c["ts"] for c in aligned["okx"]] == [0, 200]


def test_dedupe_removes_overlapping_pages():
    merged = data.dedupe([bar(200, 2, 2, 2, 2), bar(100, 1, 1, 1, 1), bar(200, 9, 9, 9, 9)])

    assert [c["ts"] for c in merged] == [100, 200]
    assert merged[1]["open"] == 9  # 나중 것이 남는다


def test_global_series_is_volume_weighted_and_excludes_krw():
    """업비트(KRW)를 섞으면 BTC 1억과 7만이 한 평균에 들어간다."""
    series = data.global_price_series(
        {
            "binance": [bar(0, 100, 100, 100, 100, volume=3.0)],
            "okx": [bar(0, 200, 200, 200, 200, volume=1.0)],
            "upbit": [bar(0, 100_000_000, 100_000_000, 100_000_000, 100_000_000, 1.0)],
        }
    )

    # (100×300 + 200×200) / (300+200) = 140
    assert series[0]["close"] == pytest.approx(140.0)
    assert series[0]["ts"] == 0


def test_single_exchange_series_is_untouched():
    """실전용(Step 4-b)은 그 거래소 가격 그대로 써야 한다."""
    candles = [bar(0, 1, 2, 0.5, 1.5)]

    assert data.single_exchange_series({"upbit": candles}, "upbit") == candles
    assert data.single_exchange_series({"upbit": candles}, "binance") == []


# --- 재현성 (Step 4 DoD) ------------------------------------------------------


def rising_candles(count, start_ts=0, volume_spike_every=7):
    """상승 추세 + 주기적 거래량 급증. 실제 지표 계산 경로를 태우기 위한 입력."""
    out = []
    price = 100.0
    for index in range(count):
        price *= 1.002
        volume = 10.0 if index % volume_spike_every else 30.0
        out.append(
            bar(
                start_ts + index * BUCKET_MS,
                price * 0.999,
                price * 1.004,
                price * 0.996,
                price,
                volume,
            )
        )
    return out


def test_same_input_produces_identical_results():
    """DoD 재현성 — 신호 산출부터 매매까지 실제 경로를 두 번 돌려 비교한다."""
    per_exchange = {
        code: rising_candles(WINDOW + 60)
        for code in ("binance", "okx", "bybit", "coinbase")
    }
    params = BacktestParams(symbol="BTC", reference_exchange=GLOBAL_CONSENSUS)

    first = run_backtest(per_exchange, params)
    second = run_backtest(per_exchange, params)

    assert first.as_dict() == second.as_dict()
    assert first.bars_evaluated == second.bars_evaluated > 0
    assert first.equity_curve == second.equity_curve


def test_result_is_serializable_for_storage():
    """`backtest_logs.params_json` 에 그대로 들어가야 한다."""
    import json

    per_exchange = {code: rising_candles(WINDOW + 30) for code in ("binance", "okx", "bybit")}
    result = run_backtest(
        per_exchange, BacktestParams(symbol="BTC", reference_exchange=GLOBAL_CONSENSUS)
    )

    encoded = json.dumps(result.as_dict(), ensure_ascii=False)

    assert json.loads(encoded)["params"]["reference_exchange"] == GLOBAL_CONSENSUS
    assert json.loads(encoded)["params"]["ai_score_included"] is False


def test_store_params_line_up_with_the_columns():
    from trading_engine.backtest import store as backtest_store

    per_exchange = {code: rising_candles(WINDOW + 30) for code in ("binance", "okx", "bybit")}
    result = run_backtest(
        per_exchange, BacktestParams(symbol="BTC", reference_exchange=GLOBAL_CONSENSUS)
    )

    params = backtest_store.build_params(result, user_id=1)

    assert len(params) == backtest_store.INSERT_SQL.count("%s")
    assert params[2] == GLOBAL_CONSENSUS


# --- 거래소 커버리지 보정 -----------------------------------------------------


def test_short_coverage_exchange_is_dropped():
    """전 거래소 교집합만 쓰면 30일 요청이 가장 짧은 거래소에 맞춰 조용히 줄어든다."""
    kept = drop_short_coverage(
        {
            "binance": flat_series(576),
            "okx": flat_series(576),
            "bybit": flat_series(576),
            "coinbase": flat_series(576),
            "upbit": flat_series(200),  # 실측: 업비트는 한 번에 200봉이 상한
        }
    )

    assert set(kept) == {"binance", "okx", "bybit", "coinbase"}


def test_coverage_filter_never_drops_below_the_consensus_minimum():
    """3개 미만이면 전 구간이 HOLD 로 강등된다. 구간이 짧더라도 표본을 지킨다."""
    per_exchange = {
        "binance": flat_series(576),
        "okx": flat_series(100),
        "bybit": flat_series(100),
    }

    assert drop_short_coverage(per_exchange) == per_exchange


def test_similar_coverage_is_left_alone():
    per_exchange = {code: flat_series(570 + i) for i, code in enumerate(("a", "b", "c", "d"))}

    assert drop_short_coverage(per_exchange) == per_exchange
