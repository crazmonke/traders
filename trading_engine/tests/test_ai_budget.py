"""AI 호출 예산 단위 테스트 — off / seed / full 과 조회자 우선 규칙.

돈이 나가는 판정이라, "부르지 말아야 할 때 부르지 않는가"를 양쪽에서 본다.
"""

import pytest

from trading_engine.market.redis_store import RedisStore, ai_call_key, ai_seed_key, viewer_key
from trading_engine.strategy.ai_budget import (
    MODE_FULL,
    MODE_OFF,
    MODE_SEED,
    SECONDS_PER_DAY,
    AiBudget,
    resolve_mode,
)
from trading_engine.strategy.signal_engine import SignalEngine
from trading_engine.strategy.signal_pipeline import SignalPipeline
from trading_engine.market.market_manager import MarketManager

from trading_engine.tests.test_signal_pipeline import ANALYSIS, _FakeRedis
from trading_engine.tests.test_strategy import BULLISH, USD_EXCHANGES, make_indicators


def make_budget(mode, seed_calls=5, client=None):
    store = RedisStore(client=client or _FakeRedis())
    return AiBudget(store, mode=mode, seed_calls_per_symbol=seed_calls), store


# --- 모드 해석 ---------------------------------------------------------------


def test_known_modes_pass_through():
    for mode in (MODE_OFF, MODE_SEED, MODE_FULL):
        assert resolve_mode(mode) == mode
    assert resolve_mode(" FULL ") == MODE_FULL


def test_unknown_mode_falls_back_to_the_cheapest():
    """오타 하나로 full 이 되면 조용히 돈이 나간다. 반대 방향 실수가 낫다."""
    assert resolve_mode("fulll") == MODE_SEED
    assert resolve_mode("") == MODE_SEED
    assert resolve_mode(None) == MODE_SEED


# --- 모드별 판정 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_never_allows_and_skips_redis():
    budget, _ = make_budget(MODE_OFF)
    assert budget.enabled is False
    assert await budget.allow("BTC") is False


@pytest.mark.asyncio
async def test_full_always_allows():
    budget, _ = make_budget(MODE_FULL)
    assert await budget.allow("BTC") is True
    assert await budget.allow("BTC") is True


@pytest.mark.asyncio
async def test_seed_allows_once_per_interval_per_symbol():
    budget, store = make_budget(MODE_SEED, seed_calls=5)

    assert await budget.allow("BTC") is True
    assert await budget.allow("BTC") is False  # 간격 안에서는 한 번뿐
    assert await budget.allow("ETH") is True  # 심볼별로 따로 센다

    assert store.client.strings[ai_seed_key("BTC")] == "1"


def test_seed_interval_spreads_calls_over_the_day():
    """하루 5건이면 4.8시간 간격. 오전에 하루치를 몰아 쓰면 통계가 편향된다."""
    budget, _ = make_budget(MODE_SEED, seed_calls=5)
    assert budget.seed_interval_sec == SECONDS_PER_DAY // 5
    assert budget.seed_interval_sec / 3600 == pytest.approx(4.8)


@pytest.mark.asyncio
async def test_seed_quota_of_zero_never_calls_on_its_own():
    budget, _ = make_budget(MODE_SEED, seed_calls=0)
    assert budget.seed_interval_sec == 0
    assert await budget.allow("BTC") is False


# --- 조회자 우선 (Step 6·9 연결 지점) ---------------------------------------


@pytest.mark.asyncio
async def test_viewer_bypasses_the_seed_quota():
    """손님이 온 가게만 전등을 켠다 — 이 기능의 원래 목적."""
    fake = _FakeRedis()
    budget, _ = make_budget(MODE_SEED, seed_calls=5, client=fake)
    fake.strings[viewer_key("BTC")] = "1"  # Step 9 대시보드가 남길 표시

    assert await budget.allow("BTC") is True
    assert await budget.allow("BTC") is True  # 쿼터를 쓰지 않는다
    assert ai_seed_key("BTC") not in fake.strings


@pytest.mark.asyncio
async def test_mark_viewer_writes_the_key_the_budget_reads():
    """Step 6·9 가 쓰는 쪽과 엔진이 읽는 쪽이 같은 키여야 한다."""
    fake = _FakeRedis()
    store = RedisStore(client=fake)
    await store.mark_viewer("BTC", 600)
    assert await store.has_viewer("BTC") is True
    assert await store.has_viewer("ETH") is False


# --- Redis 장애 시 ------------------------------------------------------------


class _BrokenRedis(_FakeRedis):
    async def set(self, key, value, ex=None, nx=False):
        raise ConnectionError("redis down")

    async def exists(self, key):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_redis_failure_denies_rather_than_spends():
    """간격을 못 지키는 상태에서 계속 부르면 예산이 샌다."""
    budget, _ = make_budget(MODE_SEED, client=_BrokenRedis())
    assert await budget.allow("BTC") is False


# --- 파이프라인과의 결합 ------------------------------------------------------


def build_pipeline(mode, seed_calls=5):
    fake = _FakeRedis()
    store = RedisStore(client=fake)
    manager = MarketManager(store)
    for exchange, symbol in USD_EXCHANGES:
        manager.record_ticker(exchange, symbol, {"quote_volume_24h": 1.0})
        manager.record_indicators(exchange, symbol, make_indicators(symbol, **BULLISH))

    calls = {"analyze": [], "save": []}

    async def analyzer(symbol, snapshot, consensus_pct, data_sources):
        calls["analyze"].append(symbol)
        return ANALYSIS

    async def saver(evaluation, ai):
        calls["save"].append(evaluation)
        return 1

    engine = SignalEngine(manager, store, min_interval_sec=0.0)
    pipeline = SignalPipeline(
        engine,
        store,
        analyzer=analyzer,
        saver=saver,
        budget=AiBudget(store, mode=mode, seed_calls_per_symbol=seed_calls),
    )
    return pipeline, calls, fake


@pytest.mark.asyncio
async def test_off_mode_still_emits_rule_signals():
    """전등을 꺼도 가게는 돌아간다 — 룰 신호는 그대로 나온다."""
    pipeline, calls, fake = build_pipeline(MODE_OFF)

    evaluation = await pipeline.run("BTC")

    assert evaluation is not None
    assert evaluation.signal_type != "HOLD"  # 신호는 정상 산출
    assert evaluation.ai_score is None
    assert calls["analyze"] == []
    assert calls["save"] == []
    # off 모드는 봉 차단 키조차 쓰지 않는다.
    assert ai_call_key("BTC", "5m") not in fake.strings


@pytest.mark.asyncio
async def test_seed_mode_calls_once_then_holds_the_quota():
    pipeline, calls, fake = build_pipeline(MODE_SEED, seed_calls=5)

    await pipeline.run("BTC")
    # 다음 봉 (봉 차단 키와 분석 캐시가 만료됐다)
    fake.strings.pop(ai_call_key("BTC", "5m"))
    fake.strings.pop("ai:result:BTC:5m")
    await pipeline.run("BTC")

    assert len(calls["analyze"]) == 1  # seed 간격이 두 번째를 막는다
    assert len(calls["save"]) == 1


@pytest.mark.asyncio
async def test_seed_slot_is_not_wasted_when_the_candle_is_already_claimed():
    """예산 확인이 봉 차단보다 앞서면, 막힐 호출에 슬롯만 태우게 된다."""
    pipeline, _, fake = build_pipeline(MODE_SEED, seed_calls=5)
    fake.strings[ai_call_key("BTC", "5m")] = "1"  # 이번 봉은 이미 처리됨

    await pipeline.run("BTC")

    assert ai_seed_key("BTC") not in fake.strings
