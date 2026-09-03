"""Step 2-a 단위 테스트 — 거래소 간 합의 · RuleEngine · Risk · Final Score.

배점표(prompt.md v2 §3.3)와 합의 규칙(§3.2)이 코드와 어긋나면 여기서 깨진다.
"""

import json

import pytest

from trading_engine.indicators.calculator import (
    BB_ABOVE_UPPER,
    Indicators,
    TREND_BEARISH,
    TREND_BULLISH,
    TREND_MIXED,
)
from trading_engine.market.market_manager import MarketManager
from trading_engine.market.redis_store import RedisStore, consensus_key
from trading_engine.strategy import consensus, risk
from trading_engine.strategy.consensus import BUY, NEUTRAL, SELL
from trading_engine.strategy.rule_engine import (
    AI_MIN_CONSENSUS_PCT,
    BASE_SCORE,
    RuleEngine,
    rsi_points,
    should_request_ai,
)
from trading_engine.strategy.signal_engine import (
    FINAL_WEIGHTS,
    SIGNAL_BUY,
    SIGNAL_HOLD,
    SIGNAL_SELL,
    SIGNAL_STRONG_BUY,
    SIGNAL_STRONG_SELL,
    SignalEngine,
    classify_signal,
    final_score,
)

engine = RuleEngine()


class _FakeRedis:
    def __init__(self):
        self.strings = {}

    async def set(self, key, value, ex=None):
        self.strings[key] = value


def make_store():
    return RedisStore(client=_FakeRedis())


NEUTRAL_SNAPSHOT = {
    "close": 100.0,
    "rsi": 50.0,
    "macd_golden_cross": False,
    "macd_dead_cross": False,
    "ma_trend": TREND_MIXED,
    "bb_lower": 90.0,
    "bb_mid": 100.0,
    "bb_upper": 110.0,
    "bollinger_position": "LOWER_HALF",
    "stochastic_k": 50.0,
    "stochastic_d": 50.0,
    "adx": 15.0,
    "cci": 0.0,
    "volume_change_rate": 0.0,
    "orderbook_imbalance": 0.0,
    "atr": 1.0,
    "vwap": 100.0,
    "vwap_divergence": 0.0,
    "prev_close": 100.0,
    "prev_macd": 0.0,
    "prev_macd_signal": 0.0,
    "prev_bb_lower": 90.0,
    "prev_bb_upper": 110.0,
    "prev_stochastic_k": 50.0,
    "prev_stochastic_d": 50.0,
    "prev_cci": 0.0,
}


def snapshot(**overrides):
    return {**NEUTRAL_SNAPSHOT, **overrides}


def points_for(result, key):
    return next(item.points for item in result.items if item.key == key)


def detail_for(result, key):
    return next(item.detail for item in result.items if item.key == key)


# --- RuleEngine: 배점표 (§3.3) ----------------------------------------------


def test_rsi_is_symmetric_around_50():
    """v3 은 RSI 를 ±10 으로 잰다. 50 이 정확히 0 이어야 중립이 흔들리지 않는다."""
    assert rsi_points(30.0)[0] == pytest.approx(10.0)  # 과매도 → +만점
    assert rsi_points(20.0)[0] == pytest.approx(10.0)  # 밖은 잘린다
    assert rsi_points(70.0)[0] == pytest.approx(-10.0)  # 과매수 → -만점
    assert rsi_points(80.0)[0] == pytest.approx(-10.0)
    assert rsi_points(50.0)[0] == pytest.approx(0.0)  # 중립
    assert rsi_points(40.0)[0] == pytest.approx(5.0)  # 선형


def test_rsi_missing_is_neutral_not_bullish():
    """워밍업 중이라는 이유로 어느 쪽으로도 기울면 안 된다."""
    assert rsi_points(None)[0] == pytest.approx(0.0)


def test_neutral_snapshot_lands_exactly_at_50():
    """§3.2 의 60/40 임계값은 중립이 50 이라는 전제 위에 있다.

    v2 는 가점 +110 / 감점 -30 의 비대칭을 기준점 40 으로 **보정**해서 맞췄다.
    v3 은 모든 항목이 0 을 중심으로 대칭이라 기준점 50 이 곧 중립이다.
    """
    result = engine.score(NEUTRAL_SNAPSHOT)
    assert result.score == pytest.approx(50.0)
    assert consensus.classify_direction(result.score) == NEUTRAL


def test_empty_snapshot_is_also_neutral():
    """지표가 하나도 없을 때 50 이 아니면, 워밍업 구간이 통째로 한쪽 신호가 된다."""
    assert engine.score({}).score == pytest.approx(50.0)


def test_quiet_market_does_not_produce_a_sell_signal():
    """아무 일도 없는 장이 합의 100% STRONG_SELL 로 나가면 AI 호출 비용까지 샌다."""
    tech = engine.score(NEUTRAL_SNAPSHOT).score
    result = consensus.compute("BTC", dict.fromkeys(USD_EXCHANGE_CODES + ("upbit",), tech))
    score = final_score(tech, result.pct, 100.0, result.direction)

    assert classify_signal(result.direction, score, True) == SIGNAL_HOLD
    assert should_request_ai(tech, result.pct) is False


# --- 대칭성: 하락 근거도 점수로 표현되어야 한다 (v3 재설계의 핵심) ---------------


def test_macd_cross_is_symmetric():
    """v2 는 데드크로스를 계산조차 하지 않아 하락에 줄 감점이 없었다."""
    assert points_for(engine.score(snapshot(macd_golden_cross=True)), "macd_cross") == 10.0
    assert points_for(engine.score(snapshot(macd_dead_cross=True)), "macd_cross") == -10.0
    assert points_for(engine.score(snapshot()), "macd_cross") == 0.0


def test_ma_alignment_is_symmetric():
    """v2 는 정배열 +15 / 역배열 0 이었다."""
    assert points_for(engine.score(snapshot(ma_trend=TREND_BULLISH)), "ma_trend") == 15.0
    assert points_for(engine.score(snapshot(ma_trend=TREND_BEARISH)), "ma_trend") == -15.0
    assert points_for(engine.score(snapshot(ma_trend=TREND_MIXED)), "ma_trend") == 0.0


def test_bearish_setup_mirrors_the_bullish_one():
    """같은 세기의 상승·하락 근거는 50 을 사이에 두고 같은 거리에 있어야 한다."""
    bull = engine.score(
        snapshot(rsi=30.0, ma_trend=TREND_BULLISH, macd_golden_cross=True,
                 adx=30.0, volume_change_rate=0.5)
    ).score
    bear = engine.score(
        snapshot(rsi=70.0, ma_trend=TREND_BEARISH, macd_dead_cross=True,
                 adx=30.0, volume_change_rate=0.5)
    ).score

    assert bull - 50.0 == pytest.approx(50.0 - bear)


def test_volume_surge_follows_the_trend_direction():
    """거래량은 방향을 모른다. v2 는 하락 중 급증까지 상승 근거로 셌다."""
    assert points_for(engine.score(snapshot(volume_change_rate=0.5, ma_trend=TREND_BULLISH)),
                      "volume") == 5.0
    assert points_for(engine.score(snapshot(volume_change_rate=0.5, ma_trend=TREND_BEARISH)),
                      "volume") == -5.0
    # 방향이 없으면 실을 곳이 없다.
    assert points_for(engine.score(snapshot(volume_change_rate=0.5, ma_trend=TREND_MIXED)),
                      "volume") == 0.0
    assert points_for(engine.score(snapshot(volume_change_rate=0.29, ma_trend=TREND_BULLISH)),
                      "volume") == 0.0


def test_adx_follows_trend_direction():
    assert points_for(engine.score(snapshot(adx=30.0, ma_trend=TREND_BULLISH)), "adx") == 10.0
    assert points_for(engine.score(snapshot(adx=30.0, ma_trend=TREND_BEARISH)), "adx") == -10.0
    # 추세는 강한데 방향이 혼조면 강화할 방향이 없다.
    assert points_for(engine.score(snapshot(adx=30.0, ma_trend=TREND_MIXED)), "adx") == 0.0
    assert points_for(engine.score(snapshot(adx=20.0, ma_trend=TREND_BULLISH)), "adx") == 0.0


def test_bollinger_reentry_and_breakout_are_symmetric():
    reentry = engine.score(
        snapshot(prev_close=85.0, prev_bb_lower=88.0, close=95.0, bb_lower=90.0)
    )
    breakout = engine.score(
        snapshot(prev_close=115.0, prev_bb_upper=110.0, close=120.0, bb_upper=112.0,
                 bollinger_position=BB_ABOVE_UPPER)
    )

    assert points_for(reentry, "bollinger") == pytest.approx(5.0)
    assert points_for(breakout, "bollinger") == pytest.approx(-5.0)


def test_bollinger_first_breakout_is_not_penalized_yet():
    """한 봉만 보고는 '지속'인지 알 수 없다. 다음 봉까지 기다린다."""
    result = engine.score(
        snapshot(prev_close=105.0, close=120.0, bb_upper=112.0, bollinger_position=BB_ABOVE_UPPER)
    )
    assert points_for(result, "bollinger") == pytest.approx(0.0)


# --- 중복 제거: 같은 것을 세 번 세지 않는다 -------------------------------------


def test_redundant_oscillators_no_longer_score():
    """RSI·스토캐스틱·CCI 는 실측 상관 r = 0.76~0.84 다. 합쳐서 40점이었다.

    v3 은 모멘텀 축을 RSI 하나로 합쳤다. 스토캐스틱과 CCI 는 화면에는 남지만
    점수는 움직이지 않는다.
    """
    overbought = engine.score(snapshot(stochastic_k=85.0, stochastic_d=80.0))
    oversold_cross = engine.score(
        snapshot(prev_stochastic_k=15.0, prev_stochastic_d=18.0,
                 stochastic_k=25.0, stochastic_d=20.0)
    )
    cci_rebound_snapshot = engine.score(snapshot(prev_cci=-150.0, cci=-120.0))

    assert points_for(overbought, "stochastic") == 0.0
    assert points_for(oversold_cross, "stochastic") == 0.0
    assert points_for(cci_rebound_snapshot, "cci") == 0.0
    # 값 자체는 사라지지 않는다 — 다시 넣을지 판단할 근거가 계속 쌓여야 한다.
    assert "85.0" in detail_for(overbought, "stochastic")
    assert "-120" in detail_for(cci_rebound_snapshot, "cci")


def test_orderbook_is_reference_only_because_it_is_untestable():
    """백테스트 표본 9,443건 중 호가가 실린 것은 0건이었다.

    검증할 수 없는 항목이 운영에서만 조용히 10점을 움직이고 있었다.
    """
    result = engine.score(snapshot(orderbook_imbalance=0.30))

    assert points_for(result, "orderbook") == 0.0
    assert "매수 우위" in detail_for(result, "orderbook")


# --- 상·하한 -------------------------------------------------------------------


def test_score_is_clamped_to_100():
    """가점 합계 상한은 55점이라 기준점 50 위에서 clamp 가 걸린다."""
    result = engine.score(
        snapshot(
            rsi=25.0,
            macd_golden_cross=True,
            macd_dead_cross=False,
            ma_trend=TREND_BULLISH,
            prev_close=85.0,
            prev_bb_lower=88.0,
            close=95.0,
            bb_lower=90.0,
            adx=30.0,
            volume_change_rate=0.5,
            orderbook_imbalance=0.3,
        )
    )
    assert result.raw_sum == pytest.approx(BASE_SCORE + 55.0)
    assert result.score == pytest.approx(100.0)


def test_score_floor_mirrors_the_ceiling():
    """v2 는 하한이 10 이었다 — 표가 표현할 수 있는 최악이 중립에서 30점 아래였다."""
    result = engine.score(
        snapshot(
            rsi=75.0,
            macd_dead_cross=True,
            ma_trend=TREND_BEARISH,
            prev_close=115.0,
            prev_bb_upper=110.0,
            close=120.0,
            bb_upper=112.0,
            bollinger_position=BB_ABOVE_UPPER,
            adx=30.0,
            volume_change_rate=0.5,
        )
    )
    assert result.raw_sum == pytest.approx(BASE_SCORE - 55.0)
    assert result.score == pytest.approx(0.0)
    assert consensus.classify_direction(result.score) == SELL


def test_score_items_explain_every_line():
    """점수만 있고 근거가 없으면 UI 에서 '왜'를 못 보여준다."""
    result = engine.score(NEUTRAL_SNAPSHOT)
    keys = [item.key for item in result.items]
    assert keys == [
        "base",
        "rsi",
        "ma_trend",
        "macd_cross",
        "adx",
        "bollinger",
        "volume",
        # 아래 넷은 점수 0 — 빼면서 지워버리면 "왜 뺐는지"도 같이 사라진다.
        "stochastic",
        "cci",
        "orderbook",
        "vwap",
    ]
    assert all(item.detail for item in result.items)
    # 내역의 합이 곧 점수여야 UI 가 "왜 이 점수인지"를 설명할 수 있다.
    assert sum(item.points for item in result.items) == pytest.approx(result.score)


# --- Consensus (§3.2) -------------------------------------------------------


def test_direction_thresholds_match_spec():
    assert consensus.classify_direction(60.0) == BUY
    assert consensus.classify_direction(59.9) == NEUTRAL
    assert consensus.classify_direction(40.0) == SELL
    assert consensus.classify_direction(40.1) == NEUTRAL
    assert consensus.classify_direction(None) == NEUTRAL


def test_consensus_pct_is_majority_over_valid_exchanges():
    result = consensus.compute(
        "BTC",
        {"binance": 80.0, "okx": 75.0, "bybit": 70.0, "coinbase": 50.0, "upbit": 20.0},
    )
    assert result.direction == BUY
    assert result.pct == pytest.approx(60.0)  # 5개 중 3개
    assert result.valid_count == 5
    assert result.is_sample_sufficient


def test_invalid_exchanges_leave_the_denominator():
    """캔들이 안 쌓인 거래소를 분모에 넣으면 합의율이 실제보다 낮아진다."""
    result = consensus.compute(
        "BTC", {"binance": 80.0, "okx": 75.0, "bybit": 70.0, "coinbase": None, "upbit": None}
    )
    assert result.valid_exchanges == ("binance", "bybit", "okx")
    assert result.pct == pytest.approx(100.0)


def test_fewer_than_three_exchanges_is_demoted():
    """§3.2 4항 — 표본 부족이면 계산하지 않는다."""
    result = consensus.compute("BTC", {"binance": 90.0, "okx": 90.0})
    assert not result.is_sample_sufficient
    assert result.pct == 0.0
    assert result.direction == NEUTRAL


def test_tie_has_no_majority_direction():
    result = consensus.compute("BTC", {"binance": 90.0, "okx": 10.0, "bybit": 50.0})
    assert result.direction == NEUTRAL
    assert result.pct == pytest.approx(100.0 / 3)


# --- AI 호출 필터 ([Step 2] 요구사항 3) --------------------------------------


@pytest.mark.parametrize(
    "tech,pct,expected",
    [
        (70.0, 50.0, True),  # 임계값 정확히
        (30.0, 50.0, True),
        (69.9, 90.0, False),  # 방향성이 약하면 부르지 않는다
        (31.0, 90.0, False),
        (85.0, 49.9, False),  # 거래소끼리 갈리면 부르지 않는다
    ],
)
def test_ai_gate(tech, pct, expected):
    assert should_request_ai(tech, pct) is expected


def test_ai_is_not_called_when_sample_is_short():
    """어차피 HOLD 로 강등될 신호에 돈을 쓰지 않는다."""
    assert should_request_ai(95.0, 100.0, sample_sufficient=False) is False
    assert AI_MIN_CONSENSUS_PCT == 50.0


# --- S_Risk -----------------------------------------------------------------


def test_risk_penalizes_volatility_by_ratio_not_absolute():
    """BTC 의 ATR 500 과 DOGE 의 ATR 0.005 를 같은 자로 재려면 비율이어야 한다."""
    assert risk.compute(500.0, 100_000.0, 5).score == pytest.approx(100.0)  # 0.5%
    assert risk.compute(0.006, 0.1, 5).score == pytest.approx(60.0)  # 6%


def test_risk_penalizes_small_sample():
    assert risk.compute(1.0, 1000.0, 5).score == pytest.approx(100.0)
    assert risk.compute(1.0, 1000.0, 4).score == pytest.approx(90.0)
    assert risk.compute(1.0, 1000.0, 3).score == pytest.approx(80.0)
    assert risk.compute(1.0, 1000.0, 2).score == pytest.approx(60.0)


def test_risk_unknown_atr_is_not_treated_as_safe():
    assert risk.compute(None, 1000.0, 5).score == pytest.approx(90.0)


def test_risk_never_goes_below_zero():
    assert risk.compute(50.0, 100.0, 0).score == pytest.approx(0.0)


# --- Final Score (§3.3) -----------------------------------------------------


def test_ai_is_not_part_of_the_score():
    """LLM 은 가격을 예측하지 못한다. 검증할 수 없는 값을 점수에 넣으면
    점수 전체가 검증 불가능해진다 (2026-09-03 결정)."""
    assert "ai" not in FINAL_WEIGHTS
    assert FINAL_WEIGHTS == {"tech": 0.45, "consensus": 0.20, "risk": 0.15}


def test_final_score_redistributes_the_spec_weights():
    """§3.3 구성비에서 AI 몫을 빼고 남은 셋을 비례 재분배한다."""
    expected = (80 * 0.45 + 60 * 0.20 + 90 * 0.15) / 0.80
    assert final_score(80.0, 60.0, 90.0, BUY) == pytest.approx(expected)


def test_final_score_is_symmetric_between_buy_and_sell():
    """확신에 찬 매도가 어정쩡한 중간 점수를 받으면 안 된다."""
    strong_buy = final_score(90.0, 100.0, 100.0, BUY)
    strong_sell = final_score(10.0, 100.0, 100.0, SELL)
    assert strong_buy == pytest.approx(strong_sell)
    assert strong_buy > 90.0


def test_signal_grades_follow_direction_and_strength():
    assert classify_signal(BUY, 85.0, True) == SIGNAL_STRONG_BUY
    assert classify_signal(BUY, 65.0, True) == SIGNAL_BUY
    assert classify_signal(BUY, 55.0, True) == SIGNAL_HOLD
    assert classify_signal(SELL, 85.0, True) == SIGNAL_STRONG_SELL
    assert classify_signal(SELL, 65.0, True) == SIGNAL_SELL
    assert classify_signal(NEUTRAL, 95.0, True) == SIGNAL_HOLD


def test_short_sample_is_always_hold():
    assert classify_signal(BUY, 99.0, False) == SIGNAL_HOLD


# --- SignalEngine 통합 -------------------------------------------------------


def make_indicators(market="BTC/USDT", **overrides):
    """지표 스냅샷 하나. 지정하지 않은 항목은 중립값이다."""
    base = {
        "market": market,
        "close": 100.0,
        "rsi": 50.0,
        "macd": 0.0,
        "macd_signal": 0.0,
        "macd_hist": 0.0,
        "macd_golden_cross": False,
        "macd_dead_cross": False,
        "ma5": 100.0,
        "ma20": 100.0,
        "ma60": 100.0,
        "ma_trend": TREND_MIXED,
        "bb_lower": 90.0,
        "bb_mid": 100.0,
        "bb_upper": 110.0,
        "bollinger_position": "LOWER_HALF",
        "stochastic_k": 50.0,
        "stochastic_d": 50.0,
        "adx": 15.0,
        "cci": 0.0,
        "orderbook_imbalance": 0.0,
        "volume_change_rate": 0.0,
        "atr": 0.5,
        "vwap": 100.0,
        "vwap_divergence": 0.0,
        "prev_close": 100.0,
        "prev_macd": 0.0,
        "prev_macd_signal": 0.0,
        "prev_bb_lower": 90.0,
        "prev_bb_upper": 110.0,
        "prev_stochastic_k": 50.0,
        "prev_stochastic_d": 50.0,
        "prev_cci": 0.0,
        "candle_count": 100,
        "candle_ts": 0,
    }
    return Indicators(**{**base, **overrides})


BULLISH = {
    "rsi": 45.0,
    "macd_golden_cross": True,
    # 글로벌 스냅샷은 불리언을 평균하지 않고 가중 평균한 MACD 에서 교차를 다시 판정한다.
    # 그래서 판정 근거가 되는 숫자까지 같이 맞춰야 거래소별 점수와 글로벌 점수가 같아진다.
    "macd": 1.0,
    "macd_signal": 0.0,
    "prev_macd": -1.0,
    "prev_macd_signal": 0.0,
    "ma_trend": TREND_BULLISH,
    "ma5": 102.0,
    "ma20": 101.0,
    "ma60": 100.0,
    "adx": 30.0,
}

USD_EXCHANGES = (
    ("binance", "BTC/USDT"),
    ("okx", "BTC/USDT"),
    ("bybit", "BTC/USDT"),
    ("coinbase", "BTC/USD"),
)
USD_EXCHANGE_CODES = tuple(code for code, _ in USD_EXCHANGES)


def build_engine(records):
    manager = MarketManager(make_store())
    for exchange, symbol, indicators in records:
        manager.record_ticker(exchange, symbol, {"quote_volume_24h": 1.0})
        manager.record_indicators(exchange, symbol, indicators)
    return manager, SignalEngine(manager, make_store())


def test_engine_agrees_across_exchanges_and_buys():
    records = [(code, sym, make_indicators(sym, **BULLISH)) for code, sym in USD_EXCHANGES]
    records.append(("upbit", "BTC/KRW", make_indicators("BTC/KRW", **BULLISH)))
    _, signal_engine = build_engine(records)

    evaluation = signal_engine.evaluate("BTC")

    assert evaluation.consensus.valid_count == 5  # 업비트도 합의에는 들어간다
    assert evaluation.consensus.pct == pytest.approx(100.0)
    assert evaluation.direction == BUY
    # 50(기준) + 2.5(RSI 45) + 15(정배열) + 10(골든크로스) + 10(ADX)
    assert evaluation.tech.score == pytest.approx(87.5)
    assert evaluation.signal_type == SIGNAL_STRONG_BUY
    assert evaluation.needs_ai is True


def test_engine_demotes_to_hold_when_only_two_exchanges_have_data():
    records = [
        ("binance", "BTC/USDT", make_indicators("BTC/USDT", **BULLISH)),
        ("okx", "BTC/USDT", make_indicators("BTC/USDT", **BULLISH)),
        # 아직 워밍업 중 — 지표가 없다.
        ("bybit", "BTC/USDT", make_indicators("BTC/USDT", rsi=None)),
    ]
    _, signal_engine = build_engine(records)

    evaluation = signal_engine.evaluate("BTC")

    assert evaluation.consensus.valid_count == 2
    assert evaluation.signal_type == SIGNAL_HOLD
    assert evaluation.consensus.pct == 0.0
    assert "표본 부족" in evaluation.demoted_reason
    assert evaluation.needs_ai is False


def test_engine_reports_per_exchange_scores_for_data_sources_json():
    records = [(code, sym, make_indicators(sym, **BULLISH)) for code, sym in USD_EXCHANGES]
    records.append(("upbit", "BTC/KRW", make_indicators("BTC/KRW")))  # 중립
    _, signal_engine = build_engine(records)

    sources = signal_engine.evaluate("BTC").data_sources()

    assert set(sources["exchanges"]) == {"binance", "okx", "bybit", "coinbase", "upbit"}
    assert sources["per_exchange_tech_score"]["upbit"] == pytest.approx(50.0)
    assert sources["directions"] == {BUY: 4, SELL: 0, NEUTRAL: 1}


def test_engine_returns_none_without_any_usd_source():
    _, signal_engine = build_engine([("upbit", "BTC/KRW", make_indicators("BTC/KRW"))])
    assert signal_engine.evaluate("BTC") is None


@pytest.mark.asyncio
async def test_publish_writes_consensus_key():
    """prompt.md v2 §3.1 의 `consensus:{symbol}:{tf}` 키."""
    records = [(code, sym, make_indicators(sym, **BULLISH)) for code, sym in USD_EXCHANGES]
    manager = MarketManager(make_store())
    for exchange, symbol, indicators in records:
        manager.record_ticker(exchange, symbol, {"quote_volume_24h": 1.0})
        manager.record_indicators(exchange, symbol, indicators)
    fake = _FakeRedis()
    signal_engine = SignalEngine(manager, RedisStore(client=fake))

    evaluation = await signal_engine.publish("BTC")

    assert evaluation is not None
    cached = json.loads(fake.strings[consensus_key("BTC", "5m")])
    assert cached["symbol"] == "BTC"
    assert cached["signal_type"] == evaluation.signal_type
    assert cached["exchange_consensus_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_publish_is_throttled_per_symbol():
    """지표는 거래소마다 갱신된다. 그때마다 다시 계산하면 초당 여러 번이 된다."""
    manager = MarketManager(make_store())
    manager.record_indicators("binance", "BTC/USDT", make_indicators())
    signal_engine = SignalEngine(manager, make_store(), min_interval_sec=60.0)

    assert await signal_engine.publish("BTC") is not None
    assert await signal_engine.publish("BTC") is None
    assert await signal_engine.publish("BTC", force=True) is not None


def test_with_ai_records_the_score_without_changing_the_signal():
    """AI 는 설명 담당이다. 점수와 등급은 룰이 정한 것 그대로 남는다."""
    records = [(code, sym, make_indicators(sym, **BULLISH)) for code, sym in USD_EXCHANGES]
    _, signal_engine = build_engine(records)
    before = signal_engine.evaluate("BTC")

    after = before.with_ai(20.0)  # AI 가 정반대로 봐도

    assert before.ai_score is None
    assert after.ai_score == 20.0
    assert after.final_score == before.final_score
    assert after.signal_type == before.signal_type


# --- 글로벌 집계가 배점 입력을 갖추는지 -------------------------------------


def test_global_snapshot_carries_every_key_the_rule_engine_reads():
    """가중 평균 스냅샷에 키가 빠지면 배점이 조용히 0점이 된다."""
    manager = MarketManager(make_store())
    manager.record_ticker("binance", "BTC/USDT", {"quote_volume_24h": 1.0})
    manager.record_indicators("binance", "BTC/USDT", make_indicators(**BULLISH))

    snapshot_dict = manager.aggregate("BTC")

    for key in NEUTRAL_SNAPSHOT:
        assert key in snapshot_dict, key
    # 같은 지표라면 거래소별 점수와 글로벌 점수가 같아야 한다.
    per_exchange = engine.score(make_indicators(**BULLISH).as_dict()).score
    assert engine.score(snapshot_dict).score == pytest.approx(per_exchange)
