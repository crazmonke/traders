"""Step 2-b 단위 테스트 — AI 프롬프트/응답 검증 · 저장 · publish · 중복 호출 차단.

OpenAI 를 실제로 부르지 않는다. `SignalPipeline` 에 analyzer/saver 를 주입해
"언제 부르고, 언제 안 부르고, 응답이 이상하면 어떻게 하는가"만 본다.
"""

import json

import pytest

from trading_engine.ai import prompt as ai_prompt
from trading_engine.ai.analyzer import (
    PROBABILITY_SUM_MAX,
    PROBABILITY_SUM_MIN,
    AiAnalysis,
    normalize_probabilities,
    parse,
)
from trading_engine.market.market_manager import MarketManager
from trading_engine.market.redis_store import (
    RedisStore,
    SIGNAL_CHANNEL,
    ai_call_key,
    ai_result_key,
    consensus_key,
)
from trading_engine.strategy import store as signal_store
from trading_engine.strategy.ai_budget import MODE_FULL, AiBudget
from trading_engine.strategy.signal_engine import SIGNAL_HOLD, SignalEngine
from trading_engine.strategy.signal_pipeline import SignalPipeline

# Step 2-a 테스트의 지표 픽스처를 그대로 쓴다. 같은 스냅샷에서 이어지는 단계라
# 픽스처가 갈라지면 두 파일이 서로 다른 신호를 검증하게 된다.
from trading_engine.tests.test_strategy import (
    BULLISH,
    USD_EXCHANGES,
    make_indicators,
)


class _FakeRedis:
    """set(nx) 와 publish 까지 흉내내는 가짜 Redis."""

    def __init__(self):
        self.strings = {}
        self.published = []

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key):
        return self.strings.get(key)

    async def exists(self, key):
        return 1 if key in self.strings else 0

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


ANALYSIS = AiAnalysis(
    signal="BUY",
    ai_score=78.0,
    up_prob=61.0,
    sideways_prob=27.0,
    down_prob=12.0,
    reasons=("정배열 유지", "5개 거래소 합의 100%"),
    risks=("단기 과매수 구간 접근",),
    model="test-model",
)


def build(analysis=ANALYSIS, indicators=None):
    """BULLISH 지표 5개 거래소 + 파이프라인. 호출 기록을 함께 돌려준다."""
    fake = _FakeRedis()
    store = RedisStore(client=fake)
    manager = MarketManager(store)
    for exchange, symbol in USD_EXCHANGES:
        manager.record_ticker(exchange, symbol, {"quote_volume_24h": 1.0})
        manager.record_indicators(
            exchange, symbol, indicators or make_indicators(symbol, **BULLISH)
        )
    manager.record_indicators(
        "upbit", "BTC/KRW", indicators or make_indicators("BTC/KRW", **BULLISH)
    )

    calls = {"analyze": [], "save": []}

    async def analyzer(symbol, snapshot, consensus_pct, data_sources):
        calls["analyze"].append((symbol, consensus_pct, tuple(data_sources)))
        return analysis

    async def saver(evaluation, ai):
        calls["save"].append(evaluation)
        return 4242

    engine = SignalEngine(manager, store, min_interval_sec=0.0)
    # 이 파일은 파이프라인 동작을 본다. 예산 정책은 test_ai_budget.py 가 따로 검증한다.
    pipeline = SignalPipeline(
        engine,
        store,
        analyzer=analyzer,
        saver=saver,
        budget=AiBudget(store, mode=MODE_FULL),
    )
    return pipeline, calls, fake


# --- §4 프롬프트 -------------------------------------------------------------


def test_user_payload_matches_spec_field_names_and_units():
    payload = ai_prompt.build_user_payload(
        "BTC",
        {
            "price": 61420.5,
            "upbit_price": 84_300_000.0,
            "rsi": 52.313,
            "macd_golden_cross": True,
            "bollinger_position": "UPPER_HALF",
            "stochastic_k": 62.1,
            "stochastic_d": 58.4,
            "adx": 28.3,
            "cci": 140.2,
            "volume_change_rate": 0.372,
            "orderbook_imbalance": 0.214,
            "ma_trend": "bullish",
        },
        82.0,
        ["binance", "okx"],
    )

    assert payload["symbol"] == "BTC"
    assert payload["global_price_usd"] == 61420.5
    assert payload["upbit_price_krw"] == 84_300_000.0
    assert payload["rsi_14"] == 52.31
    assert payload["macd_status"] == "GOLDEN_CROSS"
    # 비율이 아니라 퍼센트로 보낸다 (§4 예시: 37.2 / 21.4)
    assert payload["volume_surge_pct"] == 37.2
    assert payload["orderbook_imbalance"] == 21.4
    assert payload["ma_trend"] == "BULLISH"
    assert payload["exchange_consensus_pct"] == 82.0
    assert payload["data_sources"] == ["binance", "okx"]


def test_macd_status_falls_back_to_signal_line_position():
    assert ai_prompt.macd_status({"macd": 1.0, "macd_signal": 0.5}) == "ABOVE_SIGNAL"
    assert ai_prompt.macd_status({"macd": 0.1, "macd_signal": 0.5}) == "BELOW_SIGNAL"
    assert ai_prompt.macd_status({}) == "UNKNOWN"


def test_response_schema_avoids_keywords_strict_mode_rejects():
    """strict 모드는 minimum/maximum/minItems/maxItems 를 거부한다."""
    text = json.dumps(ai_prompt.RESPONSE_SCHEMA)
    for keyword in ("minimum", "maximum", "minItems", "maxItems"):
        assert keyword not in text
    assert ai_prompt.RESPONSE_SCHEMA["additionalProperties"] is False


# --- AI 응답 검증 ------------------------------------------------------------


VALID_RESPONSE = {
    "signal": "BUY",
    "ai_score": 78,
    "probabilities": {"up": 61.0, "sideways": 27.0, "down": 12.0},
    "reasons": ["정배열 유지", "거래소 합의 100%"],
    "risks": ["과매수 접근"],
}


def test_parse_accepts_a_well_formed_response():
    analysis = parse(VALID_RESPONSE, "test-model")
    assert analysis is not None
    assert analysis.signal == "BUY"
    assert analysis.ai_score == 78.0
    assert analysis.probability_sum == pytest.approx(100.0)
    assert analysis.is_probability_sum_valid


def test_fraction_probabilities_are_converted_to_percent():
    """§4 가 단위를 못 박지 않아 모델이 0.73/0.16/0.11 로 준다 (gpt-5-mini 실측)."""
    analysis = parse(
        {**VALID_RESPONSE, "probabilities": {"up": 0.73, "sideways": 0.16, "down": 0.11}},
        "test-model",
    )
    assert analysis.up_prob == pytest.approx(73.0)
    assert analysis.probability_sum == pytest.approx(100.0)
    assert analysis.is_probability_sum_valid


def test_broken_distribution_is_left_alone_for_the_sum_check():
    """단위를 다르게 쓴 것과 분포를 못 낸 것은 다르다. 후자는 걸러져야 한다."""
    assert normalize_probabilities(80.0, 40.0, 30.0) == (80.0, 40.0, 30.0)
    assert normalize_probabilities(0.2, 0.2, 0.2) == (0.2, 0.2, 0.2)


def test_extra_reasons_are_trimmed_not_rejected():
    """이미 낸 호출 비용을 개수 초과로 버리지 않는다. 중요한 것부터 앞에 온다."""
    analysis = parse(
        {**VALID_RESPONSE, "reasons": [f"근거{i}" for i in range(7)], "risks": ["a", "b", "c", "d"]},
        "test-model",
    )
    assert analysis.reasons == ("근거0", "근거1", "근거2", "근거3", "근거4")
    assert analysis.risks == ("a", "b", "c")


@pytest.mark.parametrize(
    "override",
    [
        {"signal": "MOON"},  # 스펙에 없는 등급
        {"ai_score": 300},  # 0~100 밖
        {"ai_score": "높음"},  # 숫자가 아님
        {"probabilities": {"up": -5.0, "sideways": 50.0, "down": 55.0}},  # 음수 확률
        {"reasons": ["하나뿐"]},  # §4 는 최소 2개
        {"risks": []},  # §4 는 최소 1개
    ],
)
def test_parse_rejects_out_of_spec_responses(override):
    """스키마를 통과해도 값이 이상할 수 있다. 근거 없는 점수를 저장하면 안 된다."""
    assert parse({**VALID_RESPONSE, **override}, "test-model") is None


def test_probability_sum_bounds_are_inclusive():
    def sum_of(total):
        return AiAnalysis(
            "BUY", 70.0, total, 0.0, 0.0, ("a", "b"), ("c",), "m"
        ).is_probability_sum_valid

    assert sum_of(PROBABILITY_SUM_MIN)
    assert sum_of(PROBABILITY_SUM_MAX)
    assert not sum_of(PROBABILITY_SUM_MIN - 0.1)
    assert not sum_of(PROBABILITY_SUM_MAX + 0.1)


# --- 파이프라인 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_strong_signal_is_analyzed_saved_and_published():
    pipeline, calls, fake = build()

    evaluation = await pipeline.run("BTC")

    assert len(calls["analyze"]) == 1
    symbol, consensus_pct, sources = calls["analyze"][0]
    assert symbol == "BTC"
    assert consensus_pct == pytest.approx(100.0)
    assert "upbit" in sources
    assert len(calls["save"]) == 1
    assert evaluation.ai_score == 78.0

    channel, message = fake.published[0]
    assert channel == SIGNAL_CHANNEL
    payload = json.loads(message)
    assert payload["signal_id"] == 4242
    assert payload["ai"]["signal"] == "BUY"
    assert payload["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_weak_signal_never_reaches_openai():
    """게이트에 걸린 신호는 돈을 쓰지 않는다. 저장도 publish 도 하지 않는다."""
    pipeline, calls, fake = build(indicators=make_indicators())  # 중립 → tech 50

    evaluation = await pipeline.run("BTC")

    assert evaluation.needs_ai is False
    assert calls["analyze"] == []
    assert calls["save"] == []
    assert fake.published == []


@pytest.mark.asyncio
async def test_second_run_in_the_same_candle_does_not_call_again():
    """prompt.md v2 [Step 2] 요구사항 3 — Redis TTL 키로 중복 호출을 막는다."""
    pipeline, calls, fake = build()

    await pipeline.run("BTC")
    await pipeline.run("BTC")

    assert len(calls["analyze"]) == 1
    assert ai_call_key("BTC", "5m") in fake.strings
    # 저장·publish 도 한 번뿐이다. 봉마다 한 행이어야 Step 7 이 성과를 셀 수 있다.
    assert len(calls["save"]) == 1
    assert len(fake.published) == 1


@pytest.mark.asyncio
async def test_ai_score_is_held_for_the_rest_of_the_candle():
    """AI 를 다시 안 불렀다고 등급이 오가면 안 된다 (실측: BUY ↔ STRONG_BUY 5초마다)."""
    pipeline, _, fake = build()

    first = await pipeline.run("BTC")
    second = await pipeline.run("BTC")

    assert second.ai_score == first.ai_score == ANALYSIS.ai_score
    assert second.signal_type == first.signal_type
    # 캐시에도 AI 반영본이 남아야 한다. 룰만 반영한 값이 덮어쓰면 대시보드가 뒤집힌다.
    cached = json.loads(fake.strings[consensus_key("BTC", "5m")])
    assert cached["ai_score"] == ANALYSIS.ai_score


@pytest.mark.asyncio
async def test_next_candle_calls_ai_again():
    """TTL 이 지나면 다음 봉이다. 낡은 AI 점수를 계속 쓰면 안 된다."""
    pipeline, calls, fake = build()

    await pipeline.run("BTC")
    # 차단 키와 분석 캐시는 같은 TTL 이라 함께 사라진다.
    fake.strings.pop(ai_call_key("BTC", "5m"))
    fake.strings.pop(ai_result_key("BTC", "5m"))
    await pipeline.run("BTC")

    assert len(calls["analyze"]) == 2
    assert len(calls["save"]) == 2


@pytest.mark.asyncio
async def test_restart_mid_candle_still_reuses_the_analysis():
    """차단 키만 남고 분석이 사라지면, 그 봉 내내 AI 없는 신호가 나간다 (실측 후 보완)."""
    first_pipeline, _, fake = build()
    await first_pipeline.run("BTC")

    # 같은 Redis 를 보는 새 프로세스. 프로세스 메모리에는 아무것도 없다.
    restarted, calls, _ = build()
    restarted._store = first_pipeline._store
    restarted._engine._store = first_pipeline._store

    evaluation = await restarted.run("BTC")

    assert calls["analyze"] == []  # 다시 부르지 않는다
    assert evaluation.ai_score == ANALYSIS.ai_score  # 그래도 AI 는 반영된다


@pytest.mark.asyncio
async def test_broken_probability_does_not_weaken_the_rule_signal():
    broken = AiAnalysis(
        signal="STRONG_BUY",
        ai_score=95.0,
        up_prob=80.0,
        sideways_prob=40.0,
        down_prob=30.0,  # 합계 150
        reasons=("a", "b"),
        risks=("c",),
        model="test-model",
    )
    pipeline, calls, _ = build(analysis=broken)

    evaluation = await pipeline.run("BTC")

    # 룰이 낸 신호를 LLM 이 JSON 을 잘못 만들었다는 이유로 약화시키지 않는다.
    assert evaluation.signal_type != SIGNAL_HOLD
    # 대신 그 확률은 신뢰할 수 없다고 표시하고, 화면이 그것을 보고 숨긴다.
    assert "확률 표시 안 함" in evaluation.demoted_reason
    assert len(calls["save"]) == 1


@pytest.mark.asyncio
async def test_failed_analysis_saves_nothing():
    pipeline, calls, fake = build(analysis=None)

    evaluation = await pipeline.run("BTC")

    assert evaluation.ai_score is None
    assert calls["save"] == []
    assert fake.published == []


# --- ai_signals 저장 ---------------------------------------------------------


def make_evaluation():
    fake = _FakeRedis()
    store = RedisStore(client=fake)
    manager = MarketManager(store)
    for exchange, symbol in USD_EXCHANGES:
        manager.record_ticker(exchange, symbol, {"quote_volume_24h": 1.0})
        manager.record_indicators(exchange, symbol, make_indicators(symbol, **BULLISH))
    engine = SignalEngine(manager, store)
    return engine.evaluate("BTC").with_ai(ANALYSIS.ai_score)


def test_insert_params_line_up_with_the_columns():
    """자리표시자 개수와 값 개수가 어긋나면 운영에서만 터진다."""
    params = signal_store.build_params(make_evaluation(), ANALYSIS)
    assert len(params) == signal_store.INSERT_SQL.count("%s")


def test_scores_are_clamped_for_tinyint_columns():
    assert signal_store._score(92.4) == 92
    assert signal_store._score(140.0) == 100
    assert signal_store._score(-3.0) == 0
    assert signal_store._score(None) is None


def test_volume_change_is_stored_as_percent_and_clamped():
    """거래량이 0 에 가까운 봉 다음의 급증은 DECIMAL(8,2) 를 넘긴다."""
    assert signal_store._percent(0.372, 999_999.99) == pytest.approx(37.2)
    assert signal_store._percent(50_000.0, 999_999.99) == pytest.approx(999_999.99)


def test_data_sources_json_keeps_the_ai_opinion():
    """저장되는 signal_type 은 룰 엔진 값이라, AI 원래 의견은 여기 남겨야 비교할 수 있다."""
    sources = json.loads(
        signal_store.data_sources_json(make_evaluation(), ANALYSIS)
    )
    assert sources["ai"]["signal"] == "BUY"
    assert sources["ai"]["model"] == "test-model"
    assert set(sources["exchanges"]) == {"binance", "okx", "bybit", "coinbase"}
    assert sources["per_exchange_tech_score"]["binance"] == pytest.approx(87.5)


@pytest.mark.asyncio
async def test_signal_without_a_global_price_is_not_saved():
    """entry_price_global 이 NOT NULL 이다. 가격 없는 행을 만들 수 없다."""
    import dataclasses

    evaluation = make_evaluation()
    without_price = dataclasses.replace(
        evaluation, snapshot={**evaluation.snapshot, "price": None}
    )
    assert await signal_store.save_signal(without_price, ANALYSIS) is None
