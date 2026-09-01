"""Step 1-b 캔들·지표 계층 단위 테스트."""

import json
import math

import pytest

from trading_engine.indicators import calculator
from trading_engine.indicators.calculator import (
    MIN_MACD_CANDLES,
    TREND_BEARISH,
    TREND_BULLISH,
    TREND_MIXED,
    TREND_UNKNOWN,
    classify_ma_trend,
    compute_orderbook_imbalance,
    compute_volume_change_rate,
)
from trading_engine.indicators.engine import IndicatorEngine
from trading_engine.market.candle_feed import CandleFeed, bucket_start, parse_rest_candle
from trading_engine.market.redis_store import RedisStore, candles_key, indicators_key

MINUTE_MS = 60 * 1000
BUCKET_MS = 5 * MINUTE_MS


def make_candles(closes, start_ts=0, volume=1.0):
    """종가만 지정해 캔들 리스트를 만든다."""
    return [
        {
            "ts": start_ts + index * BUCKET_MS,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
        }
        for index, close in enumerate(closes)
    ]


# --- 캔들 집계 ---------------------------------------------------------------


def test_bucket_start_floors_to_five_minute_grid():
    assert bucket_start(0) == 0
    assert bucket_start(4 * MINUTE_MS + 59_000) == 0
    assert bucket_start(5 * MINUTE_MS) == BUCKET_MS
    assert bucket_start(12 * MINUTE_MS) == 2 * BUCKET_MS


def test_parse_rest_candle_uses_candle_start_time_not_last_trade():
    parsed = parse_rest_candle(
        {
            "candle_date_time_utc": "2026-09-01T00:05:00",
            "opening_price": 100.0,
            "high_price": 110.0,
            "low_price": 95.0,
            "trade_price": 105.0,
            "candle_acc_trade_volume": 3.5,
            "timestamp": 1_788_221_040_000,
        }
    )

    assert parsed["ts"] == 1_788_221_100_000  # 2026-09-01T00:05:00Z
    assert parsed["open"] == 100.0
    assert parsed["high"] == 110.0
    assert parsed["low"] == 95.0
    assert parsed["close"] == 105.0
    assert parsed["volume"] == 3.5


def test_update_from_tick_accumulates_into_current_candle():
    feed = CandleFeed(["KRW-BTC"])

    _, opened = feed.update_from_tick("KRW-BTC", {"trade_price": 100, "timestamp": 0, "trade_volume": 1})
    feed.update_from_tick("KRW-BTC", {"trade_price": 120, "timestamp": 60_000, "trade_volume": 2})
    candle, rolled = feed.update_from_tick(
        "KRW-BTC", {"trade_price": 90, "timestamp": 120_000, "trade_volume": 3}
    )

    assert opened is True
    assert rolled is False
    assert len(feed.candles("KRW-BTC")) == 1
    assert (candle["open"], candle["high"], candle["low"], candle["close"]) == (100, 120, 90, 90)
    assert candle["volume"] == 6


def test_update_from_tick_opens_new_candle_on_bucket_rollover():
    feed = CandleFeed(["KRW-BTC"])
    feed.update_from_tick("KRW-BTC", {"trade_price": 100, "timestamp": 0, "trade_volume": 1})

    candle, rolled = feed.update_from_tick(
        "KRW-BTC", {"trade_price": 130, "timestamp": BUCKET_MS, "trade_volume": 1}
    )

    assert rolled is True
    assert len(feed.candles("KRW-BTC")) == 2
    assert candle["open"] == 130 and candle["ts"] == BUCKET_MS


def test_update_from_tick_ignores_late_tick_from_closed_candle():
    feed = CandleFeed(["KRW-BTC"])
    feed.update_from_tick("KRW-BTC", {"trade_price": 100, "timestamp": 0})
    feed.update_from_tick("KRW-BTC", {"trade_price": 130, "timestamp": BUCKET_MS})

    feed.update_from_tick("KRW-BTC", {"trade_price": 999, "timestamp": 60_000})

    closes = [candle["close"] for candle in feed.candles("KRW-BTC")]
    assert closes == [100, 130]


def test_update_from_tick_skips_payload_without_price_or_timestamp():
    feed = CandleFeed(["KRW-BTC"])

    assert feed.update_from_tick("KRW-BTC", {"timestamp": 0}) == (None, False)
    assert feed.update_from_tick("KRW-BTC", {"trade_price": 100}) == (None, False)
    assert feed.candles("KRW-BTC") == []


def test_seed_keeps_only_the_newest_maxlen_candles():
    feed = CandleFeed(["KRW-BTC"], maxlen=3)
    feed.seed("KRW-BTC", make_candles([1, 2, 3, 4, 5]))

    assert [candle["close"] for candle in feed.candles("KRW-BTC")] == [3, 4, 5]


# --- 개별 지표 판정 ----------------------------------------------------------


def test_classify_ma_trend_matches_scoring_table():
    assert classify_ma_trend(30, 20, 10) == TREND_BULLISH
    assert classify_ma_trend(10, 20, 30) == TREND_BEARISH
    assert classify_ma_trend(30, 10, 20) == TREND_MIXED
    assert classify_ma_trend(30, 20, None) == TREND_UNKNOWN


def test_orderbook_imbalance_is_signed_ratio():
    assert compute_orderbook_imbalance(115, 85) == pytest.approx(0.15)
    assert compute_orderbook_imbalance(85, 115) == pytest.approx(-0.15)
    assert compute_orderbook_imbalance(50, 50) == 0.0
    assert compute_orderbook_imbalance(0, 0) is None
    assert compute_orderbook_imbalance(None, None) is None


def test_volume_change_rate_compares_with_previous_candle():
    import pandas as pd

    assert compute_volume_change_rate(pd.Series([100.0, 130.0])) == pytest.approx(0.3)
    assert compute_volume_change_rate(pd.Series([100.0])) is None
    assert compute_volume_change_rate(pd.Series([0.0, 5.0])) is None


def test_golden_cross_detected_only_on_the_crossing_candle():
    import pandas as pd

    macd = pd.Series([-1.0, -0.5, 0.5, 1.0])
    signal = pd.Series([0.0, 0.0, 0.0, 0.0])

    assert calculator.detect_golden_cross(macd, signal) is False  # 이미 위에 있는 상태
    assert calculator.detect_golden_cross(macd.iloc[:3], signal.iloc[:3]) is True
    assert calculator.detect_golden_cross(macd.iloc[:2], signal.iloc[:2]) is False


# --- compute() 통합 ----------------------------------------------------------


def test_compute_on_monotonic_rise_gives_overbought_rsi_and_bullish_ma():
    candles = make_candles([100 + i for i in range(80)])

    result = calculator.compute("KRW-BTC", candles, {"total_bid_size": 115, "total_ask_size": 85})

    assert result.market == "KRW-BTC"
    assert result.candle_count == 80
    assert result.close == 179
    assert result.rsi == pytest.approx(100.0)  # 하락이 없으면 RSI는 100
    assert result.ma_trend == TREND_BULLISH
    assert result.ma5 > result.ma20 > result.ma60
    assert result.orderbook_imbalance == pytest.approx(0.15)
    assert result.candle_ts == 79 * BUCKET_MS


def test_compute_on_monotonic_fall_gives_oversold_rsi_and_bearish_ma():
    candles = make_candles([200 - i for i in range(80)])

    result = calculator.compute("KRW-BTC", candles)

    assert result.rsi == pytest.approx(0.0)
    assert result.ma_trend == TREND_BEARISH
    assert result.orderbook_imbalance is None  # 호가 스냅샷 없음


def test_compute_returns_none_for_indicators_without_enough_candles():
    result = calculator.compute("KRW-BTC", make_candles([100, 101, 102]))

    assert result.candle_count == 3
    assert result.rsi is None
    assert result.macd is None
    assert result.macd_golden_cross is False
    assert result.ma5 is None and result.ma20 is None and result.ma60 is None
    assert result.ma_trend == TREND_UNKNOWN


def test_compute_tolerates_empty_candles():
    result = calculator.compute("KRW-BTC", [])

    assert result.candle_count == 0
    assert result.close is None
    assert result.ma_trend == TREND_UNKNOWN


def test_macd_appears_once_enough_candles_accumulate():
    closes = [100 + math.sin(i / 3) * 10 for i in range(MIN_MACD_CANDLES + 5)]

    result = calculator.compute("KRW-BTC", make_candles(closes))

    assert result.macd is not None
    assert result.macd_signal is not None
    assert result.macd_hist == pytest.approx(result.macd - result.macd_signal)


def test_as_dict_is_json_serializable_for_redis():
    result = calculator.compute("KRW-BTC", make_candles([100 + i for i in range(60)]))

    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["market"] == "KRW-BTC"
    assert payload["ma_trend"] == TREND_BULLISH


# --- 이벤트 파이프라인 -------------------------------------------------------


class _FakePipeline:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def rpush(self, key, *values):
        self._ops.append(("rpush", key, values))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "delete":
                self._store.lists.pop(op[1], None)
            elif op[0] == "rpush":
                self._store.lists.setdefault(op[1], []).extend(op[2])
            elif op[0] == "expire":
                self._store.ttls[op[1]] = op[2]
        self._ops.clear()


class _FakeRedis:
    def __init__(self):
        self.strings = {}
        self.lists = {}
        self.ttls = {}

    async def set(self, key, value, ex=None):
        self.strings[key] = value
        self.ttls[key] = ex

    def pipeline(self):
        return _FakePipeline(self)


@pytest.mark.asyncio
async def test_save_candles_replaces_list_with_ttl():
    fake = _FakeRedis()
    store = RedisStore(client=fake, candle_ttl=3600)

    await store.save_candles("KRW-BTC", make_candles([1, 2]))
    await store.save_candles("KRW-BTC", make_candles([3]))

    key = candles_key("KRW-BTC")
    assert key == "market:KRW-BTC:candles:5m"
    assert [json.loads(row)["close"] for row in fake.lists[key]] == [3]
    assert fake.ttls[key] == 3600


@pytest.mark.asyncio
async def test_engine_recalculates_and_publishes_to_handlers():
    fake = _FakeRedis()
    store = RedisStore(client=fake, indicator_ttl=300)
    feed = CandleFeed(["KRW-BTC"])
    feed.seed("KRW-BTC", make_candles([100 + i for i in range(60)]))
    engine = IndicatorEngine(store, feed, min_interval_sec=0.0)

    seen = []
    engine.on_indicators(lambda market, indicators: seen.append((market, indicators)) or _noop())

    await engine.handle_event(
        "orderbook", "KRW-BTC", {"total_bid_size": 115, "total_ask_size": 85}
    )

    assert len(seen) == 1
    market, indicators = seen[0]
    assert market == "KRW-BTC"
    assert indicators.orderbook_imbalance == pytest.approx(0.15)
    assert engine.latest["KRW-BTC"] is indicators

    cached = json.loads(fake.strings[indicators_key("KRW-BTC")])
    assert cached["ma_trend"] == TREND_BULLISH
    assert fake.ttls[indicators_key("KRW-BTC")] == 300


async def _noop():
    return None


@pytest.mark.asyncio
async def test_engine_throttles_recalculation_between_ticks():
    fake = _FakeRedis()
    feed = CandleFeed(["KRW-BTC"])
    feed.seed("KRW-BTC", make_candles([100 + i for i in range(60)]))
    engine = IndicatorEngine(RedisStore(client=fake), feed, min_interval_sec=60.0)

    calls = []
    engine.on_indicators(lambda market, indicators: calls.append(market) or _noop())

    # 같은 봉 안에서 연달아 들어온 틱은 첫 건만 계산한다.
    base = 60 * BUCKET_MS
    for offset in range(3):
        await engine.handle_event(
            "ticker", "KRW-BTC", {"trade_price": 160, "timestamp": base + offset, "trade_volume": 1}
        )

    assert calls == ["KRW-BTC"]


@pytest.mark.asyncio
async def test_engine_forces_recalculation_when_candle_rolls_over():
    fake = _FakeRedis()
    feed = CandleFeed(["KRW-BTC"])
    feed.seed("KRW-BTC", make_candles([100 + i for i in range(60)]))
    engine = IndicatorEngine(RedisStore(client=fake), feed, min_interval_sec=60.0)

    calls = []
    engine.on_indicators(lambda market, indicators: calls.append(market) or _noop())

    await engine.handle_event(
        "ticker", "KRW-BTC", {"trade_price": 160, "timestamp": 60 * BUCKET_MS, "trade_volume": 1}
    )
    # 스로틀 구간이지만 새 봉이 열리면 즉시 계산한다.
    await engine.handle_event(
        "ticker", "KRW-BTC", {"trade_price": 161, "timestamp": 61 * BUCKET_MS, "trade_volume": 1}
    )

    assert calls == ["KRW-BTC", "KRW-BTC"]
    assert fake.lists[candles_key("KRW-BTC")]  # 봉이 닫힐 때 캔들도 저장된다


@pytest.mark.asyncio
async def test_engine_survives_handler_failure():
    feed = CandleFeed(["KRW-BTC"])
    feed.seed("KRW-BTC", make_candles([100 + i for i in range(60)]))
    engine = IndicatorEngine(RedisStore(client=_FakeRedis()), feed, min_interval_sec=0.0)

    async def boom(market, indicators):
        raise RuntimeError("룰 엔진 폭발")

    ok = []

    async def fine(market, indicators):
        ok.append(market)

    engine.on_indicators(boom)
    engine.on_indicators(fine)

    await engine.handle_event("orderbook", "KRW-BTC", {"total_bid_size": 1, "total_ask_size": 1})

    assert ok == ["KRW-BTC"]


@pytest.mark.asyncio
async def test_prime_fills_candles_and_indicators_before_first_tick():
    fake = _FakeRedis()
    feed = CandleFeed(["KRW-BTC", "KRW-ETH"])
    feed.seed("KRW-BTC", make_candles([100 + i for i in range(60)]))
    # KRW-ETH는 시딩에 실패한 마켓 — 건너뛰고 나머지를 처리해야 한다.
    engine = IndicatorEngine(RedisStore(client=fake), feed, min_interval_sec=0.0)

    await engine.prime()

    assert len(fake.lists[candles_key("KRW-BTC")]) == 60
    assert json.loads(fake.strings[indicators_key("KRW-BTC")])["ma_trend"] == TREND_BULLISH
    assert candles_key("KRW-ETH") not in fake.lists
    assert indicators_key("KRW-ETH") not in fake.strings
    assert set(engine.latest) == {"KRW-BTC"}
