"""지표 계산 · 거래소별 지표 엔진 · 글로벌 집계 단위 테스트."""

import json
import math

import pandas as pd
import pytest

from trading_engine.indicators import calculator
from trading_engine.indicators.calculator import (
    BB_ABOVE_UPPER,
    BB_BELOW_LOWER,
    BB_LOWER_HALF,
    BB_UPPER_HALF,
    MIN_MACD_CANDLES,
    TREND_BEARISH,
    TREND_BULLISH,
    TREND_MIXED,
    TREND_UNKNOWN,
    classify_bollinger_position,
    classify_ma_trend,
    compute_cci,
    compute_orderbook_imbalance,
    compute_volume_change_rate,
)
from trading_engine.indicators.engine import IndicatorEngine
from trading_engine.market.market_manager import MarketManager, base_of, weighted_average
from trading_engine.market.redis_store import RedisStore, exchange_key, global_key

BUCKET_MS = 5 * 60 * 1000


def make_candles(closes, spread=1.0, volume=1.0, start_ts=0):
    return [
        {
            "ts": start_ts + index * BUCKET_MS,
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": volume,
        }
        for index, close in enumerate(closes)
    ]


class _FakeRedis:
    def __init__(self):
        self.strings = {}
        self.lists = {}

    async def set(self, key, value, ex=None):
        self.strings[key] = value


def make_store():
    return RedisStore(client=_FakeRedis())


# --- 개별 지표 판정 ----------------------------------------------------------


def test_classify_ma_trend_matches_scoring_table():
    assert classify_ma_trend(30, 20, 10) == TREND_BULLISH
    assert classify_ma_trend(10, 20, 30) == TREND_BEARISH
    assert classify_ma_trend(30, 10, 20) == TREND_MIXED
    assert classify_ma_trend(30, 20, None) == TREND_UNKNOWN


def test_orderbook_imbalance_is_signed_ratio():
    assert compute_orderbook_imbalance(115, 85) == pytest.approx(0.15)
    assert compute_orderbook_imbalance(85, 115) == pytest.approx(-0.15)
    assert compute_orderbook_imbalance(0, 0) is None


def test_volume_change_rate_compares_with_previous_candle():
    assert compute_volume_change_rate(pd.Series([100.0, 130.0])) == pytest.approx(0.3)
    assert compute_volume_change_rate(pd.Series([0.0, 5.0])) is None


def test_bollinger_position_covers_every_enum_value():
    # ai_signals.bollinger_position ENUM 과 값이 같아야 한다
    assert classify_bollinger_position(5, 10, 15, 20) == BB_BELOW_LOWER
    assert classify_bollinger_position(12, 10, 15, 20) == BB_LOWER_HALF
    assert classify_bollinger_position(17, 10, 15, 20) == BB_UPPER_HALF
    assert classify_bollinger_position(25, 10, 15, 20) == BB_ABOVE_UPPER
    assert classify_bollinger_position(None, 10, 15, 20) is None


def test_cci_matches_the_textbook_formula():
    """pandas_ta.cci 는 0.4.71b0 에서 부호와 크기가 어긋나 직접 구현했다."""
    close = pd.Series([100 + i * 0.3 for i in range(60)])
    high, low = close + 1, close - 1

    typical = (high + low + close) / 3
    sma = typical.rolling(20).mean()
    mad = typical.rolling(20).apply(lambda w: abs(w - w.mean()).mean(), raw=True)
    expected = ((typical - sma) / (0.015 * mad)).iloc[-1]

    assert compute_cci(high, low, close).iloc[-1] == pytest.approx(expected)
    # 상승 추세면 양수여야 한다
    assert compute_cci(high, low, close).iloc[-1] > 0


def test_cci_is_undefined_on_a_perfectly_flat_series():
    flat = pd.Series([100.0] * 40)
    assert pd.isna(compute_cci(flat + 1, flat - 1, flat).iloc[-1])


# --- compute() 통합 ----------------------------------------------------------


def test_compute_on_monotonic_rise_gives_overbought_rsi_and_bullish_ma():
    result = calculator.compute("BTC/USDT", make_candles([100 + i for i in range(80)]),
                                {"total_bid_size": 115, "total_ask_size": 85})

    assert result.rsi == pytest.approx(100.0)
    assert result.ma_trend == TREND_BULLISH
    assert result.orderbook_imbalance == pytest.approx(0.15)


def test_compute_fills_all_four_new_indicators_with_enough_candles():
    result = calculator.compute("BTC/USDT", make_candles([100 + math.sin(i / 4) * 8 for i in range(120)]))

    assert result.bb_lower < result.bb_mid < result.bb_upper
    assert result.bollinger_position in {BB_BELOW_LOWER, BB_LOWER_HALF, BB_UPPER_HALF, BB_ABOVE_UPPER}
    assert 0 <= result.stochastic_k <= 100
    assert 0 <= result.stochastic_d <= 100
    assert result.adx is not None
    assert result.cci is not None


def test_adx_separates_trend_from_chop():
    trending = calculator.compute("BTC/USDT", make_candles([100 + i * 0.5 for i in range(200)]))
    choppy = calculator.compute("BTC/USDT", make_candles([100 + (i % 2) * 0.2 for i in range(200)]))

    assert trending.adx > choppy.adx


def test_new_indicators_are_none_before_warmup():
    result = calculator.compute("BTC/USDT", make_candles([100, 101, 102]))

    assert result.bb_mid is None
    assert result.bollinger_position is None
    assert result.stochastic_k is None
    assert result.adx is None
    assert result.cci is None


def test_macd_appears_once_enough_candles_accumulate():
    closes = [100 + math.sin(i / 3) * 10 for i in range(MIN_MACD_CANDLES + 5)]
    result = calculator.compute("BTC/USDT", make_candles(closes))

    assert result.macd_hist == pytest.approx(result.macd - result.macd_signal)


def test_as_dict_is_json_serializable_for_redis():
    result = calculator.compute("BTC/USDT", make_candles([100 + i for i in range(120)]))

    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["ma_trend"] == TREND_BULLISH
    assert "cci" in payload and "adx" in payload


# --- 거래소별 지표 엔진 ------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_computes_per_exchange_and_symbol():
    fake = _FakeRedis()
    engine = IndicatorEngine(RedisStore(client=fake), min_interval_sec=0.0)
    candles = make_candles([100 + i for i in range(80)])

    await engine.handle_event("candle", "binance", "BTC/USDT", {"candles": candles})
    await engine.handle_event("candle", "upbit", "BTC/KRW", {"candles": candles})

    assert set(engine.latest) == {("binance", "BTC/USDT"), ("upbit", "BTC/KRW")}
    assert exchange_key("binance", "BTC/USDT", "indicators") in fake.strings
    assert exchange_key("upbit", "BTC/KRW", "indicators") in fake.strings


@pytest.mark.asyncio
async def test_engine_merges_orderbook_into_indicators():
    engine = IndicatorEngine(make_store(), min_interval_sec=0.0)
    candles = make_candles([100 + i for i in range(80)])

    await engine.handle_event("orderbook", "binance", "BTC/USDT",
                              {"total_bid_size": 115, "total_ask_size": 85})
    await engine.handle_event("candle", "binance", "BTC/USDT", {"candles": candles})

    assert engine.latest[("binance", "BTC/USDT")].orderbook_imbalance == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_engine_ignores_candle_event_without_candles():
    engine = IndicatorEngine(make_store(), min_interval_sec=0.0)

    await engine.handle_event("candle", "binance", "BTC/USDT", {"candles": []})

    assert engine.latest == {}


@pytest.mark.asyncio
async def test_engine_survives_handler_failure():
    engine = IndicatorEngine(make_store(), min_interval_sec=0.0)
    ok = []

    async def boom(exchange, symbol, indicators):
        raise RuntimeError("룰 엔진 폭발")

    async def fine(exchange, symbol, indicators):
        ok.append(exchange)

    engine.on_indicators(boom)
    engine.on_indicators(fine)
    await engine.handle_event("candle", "binance", "BTC/USDT",
                              {"candles": make_candles([100 + i for i in range(30)])})

    assert ok == ["binance"]


# --- 글로벌 가중 집계 --------------------------------------------------------


def test_weighted_average_uses_weights():
    assert weighted_average([(100.0, 3.0), (200.0, 1.0)]) == pytest.approx(125.0)


def test_weighted_average_falls_back_to_plain_mean_without_weights():
    assert weighted_average([(100.0, 0.0), (200.0, 0.0)]) == pytest.approx(150.0)


def test_weighted_average_skips_none_values():
    assert weighted_average([(None, 5.0), (50.0, 1.0)]) == pytest.approx(50.0)


def test_base_of_strips_quote_currency():
    assert base_of("BTC/USDT") == "BTC"
    assert base_of("BTC/KRW") == "BTC"


def make_indicators(close):
    """마지막 종가가 정확히 `close` 가 되는 상승 캔들로 지표를 만든다."""
    return calculator.compute("X", make_candles([close - 79 + i for i in range(80)]))


@pytest.mark.asyncio
async def test_global_price_excludes_krw_quoted_upbit():
    """업비트는 KRW 라 평균에 섞으면 안 된다 (BTC 1억원 vs 7만달러)."""
    fake = _FakeRedis()
    manager = MarketManager(RedisStore(client=fake))

    manager.record_ticker("binance", "BTC/USDT", {"quote_volume_24h": 1000.0})
    manager.record_ticker("upbit", "BTC/KRW", {"quote_volume_24h": 9_999_999.0})
    manager.record_indicators("binance", "BTC/USDT", make_indicators(100.0))
    manager.record_indicators("upbit", "BTC/KRW", make_indicators(100_000_000.0))

    snapshot = await manager.publish("BTC")

    assert snapshot["sources"] == ["binance"]
    assert snapshot["price"] == pytest.approx(100.0)
    # 버려지지 않고 따로 실린다
    assert snapshot["upbit_price"] == pytest.approx(100_000_000.0)
    assert json.loads(fake.strings[global_key("BTC", "price")])["source_count"] == 1


@pytest.mark.asyncio
async def test_global_price_is_volume_weighted_across_usd_exchanges():
    manager = MarketManager(make_store())
    manager.record_ticker("binance", "BTC/USDT", {"quote_volume_24h": 3.0})
    manager.record_ticker("okx", "BTC/USDT", {"quote_volume_24h": 1.0})
    manager.record_indicators("binance", "BTC/USDT", make_indicators(100.0))
    manager.record_indicators("okx", "BTC/USDT", make_indicators(200.0))

    snapshot = await manager.publish("BTC")

    assert snapshot["price"] == pytest.approx(125.0)  # (100*3 + 200*1) / 4
    assert snapshot["source_count"] == 2


@pytest.mark.asyncio
async def test_global_aggregate_separates_symbols():
    manager = MarketManager(make_store())
    manager.record_ticker("binance", "BTC/USDT", {"quote_volume_24h": 1.0})
    manager.record_ticker("binance", "ETH/USDT", {"quote_volume_24h": 1.0})
    manager.record_indicators("binance", "BTC/USDT", make_indicators(100.0))
    manager.record_indicators("binance", "ETH/USDT", make_indicators(50.0))

    assert manager.aggregate("BTC")["price"] == pytest.approx(100.0)
    assert manager.aggregate("ETH")["price"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_global_aggregate_returns_none_without_usd_sources():
    manager = MarketManager(make_store())
    manager.record_indicators("upbit", "BTC/KRW", make_indicators(100_000_000.0))

    assert manager.aggregate("BTC") is None
    assert await manager.publish("BTC") is None
