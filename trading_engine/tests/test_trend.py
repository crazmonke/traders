"""추세추종 전략 (2026-09-04 채택) 단위 테스트.

**이 파일이 지키는 것 둘.**

1. **교차한 날에만 신호를 낸다.** 상태만 보고 매일 내보내면 하루 5건씩 쌓여
   "주 1~2건"이 아니게 되고, 적중률 통계가 같은 포지션을 수백 번 센 것이 된다.
2. **룰 엔진 신호와 섞이지 않는다.** 완전히 다른 규칙으로 만들어진 다른 것이라,
   한 통계에 들어가면 어느 쪽이 맞았는지 영영 알 수 없다.

거래소·DB 를 부르지 않는다.
"""

import pytest

from trading_engine.strategy import trend
from trading_engine.strategy import trend_runner
from trading_engine.strategy.trend import MA_DAYS, TrendState, agreement, moving_average, state_from

DAY = 86_400_000


def bars(values):
    return [{"ts": i * DAY, "close": float(v)} for i, v in enumerate(values)]


FLAT = [100.0] * MA_DAYS


# --- 교차 판정 -----------------------------------------------------------------


def test_signal_only_on_the_day_it_crosses_up():
    state = state_from("BTC", bars(FLAT + [100.0, 130.0]))

    assert state.above is True and state.was_above is False
    assert state.crossed is True
    assert state.signal_type == trend.SIGNAL_ENTER


def test_signal_only_on_the_day_it_crosses_down():
    state = state_from("BTC", bars(FLAT + [100.5, 70.0]))

    assert state.crossed is True
    assert state.signal_type == trend.SIGNAL_EXIT


def test_staying_above_emits_nothing():
    """상태만 보고 매일 내보내면 주 1~2건이 아니라 매일 5건이 된다."""
    state = state_from("BTC", bars(FLAT + [130.0, 140.0]))

    assert state.above is True and state.crossed is False


def test_staying_below_emits_nothing():
    state = state_from("BTC", bars(FLAT + [70.0, 60.0]))

    assert state.above is False and state.crossed is False


# --- 이평 -----------------------------------------------------------------------


def test_moving_average_needs_a_full_window():
    """창이 안 차면 지어내지 않는다. 없는 평균으로 낸 신호는 근거가 없다."""
    assert moving_average([1.0] * (MA_DAYS - 1)) is None
    assert moving_average([2.0] * MA_DAYS) == pytest.approx(2.0)


def test_state_needs_one_extra_candle_for_the_previous_day():
    """직전 상태를 모르면 교차를 판정할 수 없다."""
    assert state_from("BTC", bars([100.0] * MA_DAYS)) is None
    assert state_from("BTC", bars([100.0] * (MA_DAYS + 1))) is not None


def test_distance_is_relative_not_absolute():
    """절대 금액은 심볼마다 자릿수가 달라 비교가 안 된다."""
    state = state_from("BTC", bars(FLAT + [100.0, 110.0]))

    assert state.distance_pct == pytest.approx(
        (state.close / state.moving_average - 1) * 100
    )


# --- 거래소 합의 ---------------------------------------------------------------


def test_agreement_counts_exchanges_above_their_own_average():
    """글로벌 계열 하나로만 판정하면 한 거래소의 이상치가 그대로 신호가 된다."""
    per = {
        "a": bars(FLAT + [130.0]),
        "b": bars(FLAT + [130.0]),
        "c": bars(FLAT + [70.0]),
    }

    assert agreement(per) == pytest.approx(200 / 3)


def test_agreement_ignores_exchanges_without_enough_candles():
    per = {"a": bars(FLAT + [130.0]), "short": bars([100.0] * 5)}

    assert agreement(per) == pytest.approx(100.0)


def test_agreement_without_any_usable_exchange_is_zero():
    assert agreement({"short": bars([100.0] * 3)}) == 0.0


# --- 룰 엔진과 섞이지 않는다 ----------------------------------------------------


def test_scoring_version_differs_from_the_rule_engine():
    """같으면 적중률 화면이 두 전략을 한 통계로 합쳐 버린다."""
    from trading_engine.strategy.versioning import SCORING_VERSION

    assert trend.STRATEGY_VERSION != SCORING_VERSION
    # `ai_signals.scoring_version` 은 VARCHAR(8) 이다.
    assert len(trend.STRATEGY_VERSION) <= 8


def test_timeframe_differs_from_the_live_engine():
    from trading_engine.market.exchange_feed import CANDLE_TIMEFRAME

    assert trend.TIMEFRAME != CANDLE_TIMEFRAME


def test_data_sources_marks_it_as_not_rule_engine():
    """덤프만 봐도 어느 전략의 신호인지 알 수 있어야 한다."""
    import json

    state = state_from("BTC", bars(FLAT + [100.0, 130.0]))
    payload = json.loads(trend_runner.data_sources_json(state, ["binance", "okx"]))

    assert payload["rule_engine"] is False
    assert payload["strategy"] == "trend-following"
    assert payload["ma_days"] == MA_DAYS
    assert payload["scoring_version"] == trend.STRATEGY_VERSION


def test_insert_fills_every_not_null_column():
    """`ai_signals` 의 NOT NULL 컬럼을 하나라도 빼면 모든 저장이 실패한다."""
    required = {
        "symbol", "timeframe", "scoring_version", "signal_type",
        "tech_score", "risk_score", "final_score",
        "entry_price_global", "exchange_consensus_pct", "data_sources_json",
    }
    columns = trend_runner.INSERT_SQL.split("ai_signals (")[1].split(") VALUES")[0]
    names = {c.strip() for c in columns.split(",") if c.strip()}

    assert required <= names
    assert trend_runner.INSERT_SQL.count("%s") == len(names)


# --- 하루 한 번 -----------------------------------------------------------------


class _ClaimRedis:
    def __init__(self):
        self.strings = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True


@pytest.mark.asyncio
async def test_same_candle_is_claimed_only_once():
    """재시작해도 같은 봉으로 두 번 내보내면 적중률 표본이 부풀려진다."""
    from trading_engine.market.redis_store import RedisStore

    store = RedisStore(client=_ClaimRedis())

    assert await trend_runner.claim(store, "BTC", 1_000) is True
    assert await trend_runner.claim(store, "BTC", 1_000) is False
    # 다음 봉은 새 슬롯이다.
    assert await trend_runner.claim(store, "BTC", 2_000) is True
    # 다른 심볼도 서로를 막지 않는다.
    assert await trend_runner.claim(store, "ETH", 1_000) is True


@pytest.mark.asyncio
async def test_redis_failure_suppresses_the_signal():
    """중복 신호가 통계를 부풀리는 것이 신호 하나를 놓치는 것보다 나쁘다."""

    class Broken:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

    from trading_engine.market.redis_store import RedisStore

    assert await trend_runner.claim(RedisStore(client=Broken()), "BTC", 1) is False
