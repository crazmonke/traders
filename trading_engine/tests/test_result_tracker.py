"""성과 추적 스케줄러 (Step 7) 단위 테스트.

**이 파일이 지키는 것은 "적중률이 부풀려지지 않는다"** 다. 적중률은 이 서비스가
광고할 수 있는 유일한 실적이라(`docs/LEGAL.md`), 재는 방식이 한 군데라도 느슨하면
대외적으로 말할 수 있는 내용 전체가 무너진다.

거래소·DB 를 부르지 않는다. 네트워크가 필요한 확인은 수동 스크립트로 한다.
"""

import pytest

from trading_engine.market.exchange_feed import CANDLE_TIMEFRAME
from trading_engine.strategy import labeling
from trading_engine.tracking import result_tracker as tracker
from trading_engine.tracking.result_tracker import PendingSignal

MIN_MS = 60_000


def bar(minute, high, low, close=None):
    return {
        "ts": minute * MIN_MS,
        "open": low,
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
        "volume": 1.0,
    }


def signal(horizon="1h", created_minute=0, signal_type="BUY", entry=100.0):
    return PendingSignal(
        signal_id=1,
        symbol="BTC",
        signal_type=signal_type,
        entry_price=entry,
        created_ts_ms=created_minute * MIN_MS,
        horizon=horizon,
    )


# --- 언제 평가할 수 있는가 ------------------------------------------------------


def test_waits_one_extra_candle_past_the_horizon():
    """마지막 봉이 닫히기 전에 판정하면 그 봉의 고가·저가가 아직 확정되지 않았다."""
    assert tracker.ready_after_minutes("5m", "5m") == 10
    assert tracker.ready_after_minutes("1h", "5m") == 65
    assert tracker.ready_after_minutes("1d", "5m") == 1445


def test_every_horizon_has_a_wait_rule():
    """horizon 을 늘리고 대기 규칙을 빼먹으면 조용히 KeyError 로 데몬이 죽는다."""
    for horizon in labeling.HORIZONS:
        assert tracker.ready_after_minutes(horizon, CANDLE_TIMEFRAME) > labeling.HORIZONS[horizon]


def test_unknown_candle_timeframe_raises():
    with pytest.raises(ValueError):
        tracker.ready_after_minutes("1h", "7m")


# --- 미래를 보지 않기 -----------------------------------------------------------


def test_the_bar_the_signal_sits_in_is_excluded():
    """신호가 걸쳐 있는 봉에는 신호 **이전** 움직임이 섞여 있다.

    그대로 쓰면 신호를 내기도 전에 닿았던 가격으로 익절이 판정된다.
    """
    series = [bar(0, 200, 50), bar(5, 101, 99), bar(10, 102, 100)]

    bars = tracker.forward_bars(series, created_ts_ms=2 * MIN_MS)

    assert [b["ts"] for b in bars] == [5 * MIN_MS, 10 * MIN_MS]


def test_a_signal_exactly_on_a_bar_open_still_excludes_that_bar():
    """경계에서 '>=' 로 새면 그 봉 전체가 신호 이후로 취급된다."""
    series = [bar(0, 200, 50), bar(5, 101, 99)]

    bars = tracker.forward_bars(series, created_ts_ms=0)

    assert [b["ts"] for b in bars] == [5 * MIN_MS]


# --- 덜 익은 판정을 남기지 않는다 -----------------------------------------------


def test_not_evaluated_when_bars_do_not_cover_the_horizon():
    """봉이 모자란 채로 판정하면 아직 닿을 수 있는 배리어를 놓치고 TIME_LIMIT 으로 굳는다."""
    partial = [bar(5, 101, 99), bar(10, 101, 99)]  # 1시간 제한인데 10분치뿐

    assert tracker.evaluate(signal(horizon="1h"), partial) is None


def test_not_evaluated_without_any_forward_bar():
    assert tracker.evaluate(signal(), [bar(0, 101, 99)]) is None
    assert tracker.evaluate(signal(), []) is None


def test_evaluated_once_the_horizon_is_covered():
    series = [bar(m, 101, 99, close=100) for m in range(5, 70, 5)]

    label = tracker.evaluate(signal(horizon="1h"), series)

    assert label is not None
    assert label.horizon == "1h"


# --- 판정은 labeling 에 위임한다 (정의가 갈라지면 안 된다) ------------------------


def test_take_profit_uses_the_shared_barrier():
    series = [bar(5, 101, 99, close=100), bar(10, 106, 100)] + [
        bar(m, 101, 99, close=100) for m in range(15, 70, 5)
    ]

    label = tracker.evaluate(signal(horizon="1h", entry=100.0), series)

    assert label.exit_reason == labeling.EXIT_TAKE_PROFIT
    assert label.return_pct == pytest.approx(labeling.TAKE_PROFIT_PCT)
    assert label.is_accurate is True


def test_stop_loss_wins_a_tie_here_too():
    """백테스트와 같은 규칙이어야 한다 — 유리한 쪽을 가정하면 적중률이 부풀려진다."""
    series = [bar(5, 110, 90)] + [bar(m, 101, 99, close=100) for m in range(10, 70, 5)]

    label = tracker.evaluate(signal(horizon="1h"), series)

    assert label.exit_reason == labeling.EXIT_STOP_LOSS
    assert label.is_accurate is False


def test_sell_signal_is_correct_when_price_falls():
    series = [bar(5, 100, 94)] + [bar(m, 96, 94, close=95) for m in range(10, 70, 5)]

    label = tracker.evaluate(signal(horizon="1h", signal_type="SELL"), series)

    assert label.exit_reason == labeling.EXIT_TAKE_PROFIT
    assert label.is_accurate is True


def test_hold_is_never_evaluated():
    """진입하지 않았으므로 맞고 틀리고가 없다. 세면 승률이 통째로 왜곡된다."""
    series = [bar(m, 110, 90, close=100) for m in range(5, 70, 5)]

    assert tracker.evaluate(signal(horizon="1h", signal_type="HOLD"), series) is None


# --- 캔들 수집 구간 -------------------------------------------------------------


def test_window_covers_every_pending_item_of_a_symbol():
    """건별로 받으면 같은 캔들을 수십 번 다시 받는다. 합쳐서 한 번에 받는다."""
    items = [
        signal(horizon="5m", created_minute=0),
        signal(horizon="1d", created_minute=10),
    ]

    since, until = tracker._window(items)

    assert since <= 0
    # 가장 늦게 끝나는 건(10분 + 1일)을 덮어야 한다.
    assert until >= (10 + labeling.HORIZONS["1d"]) * MIN_MS


def test_window_never_asks_for_the_future():
    """아직 오지 않은 구간을 요청하면 거래소마다 다르게 반응한다 — 상한을 지금으로 둔다."""
    import time

    now_ms = int(time.time() * 1000)
    recent = PendingSignal(
        signal_id=1, symbol="BTC", signal_type="BUY", entry_price=100.0,
        created_ts_ms=now_ms, horizon="1d",
    )

    _, until = tracker._window([recent])

    assert until <= now_ms + 1000


# --- SQL -----------------------------------------------------------------------


def test_pending_query_excludes_unlabeled_signals():
    """HOLD 를 SQL 에서 빼지 않으면 매 주기 같은 건을 뽑아 계속 버리게 된다."""
    placeholders = ", ".join(["%s"] * len(labeling.UNLABELED_SIGNALS))
    sql = tracker.PENDING_SQL.format(unlabeled=placeholders)

    assert "signal_type NOT IN" in sql
    assert "r.id IS NULL" in sql  # 이미 평가된 건은 다시 뽑지 않는다


def test_upsert_is_idempotent():
    """데몬이 겹쳐 돌아도 같은 (신호, horizon) 이 두 줄이 되면 안 된다."""
    assert "ON DUPLICATE KEY UPDATE" in tracker.UPSERT_SQL


# --- 분산 락 -------------------------------------------------------------------


class _LockRedis:
    """`SET NX EX` 와 Lua 해제만 흉내 내는 최소 구현. TTL 만료는 다루지 않는다."""

    def __init__(self):
        self.strings = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def eval(self, script, numkeys, key, arg):
        # `_RELEASE_LOCK` 은 "값이 같을 때만 지운다"가 전부다.
        if self.strings.get(key) == arg:
            del self.strings[key]
            return 1
        return 0


def make_lock_store():
    from trading_engine.market.redis_store import RedisStore

    return RedisStore(client=_LockRedis())


@pytest.mark.asyncio
async def test_only_one_instance_holds_the_lock():
    """두 인스턴스가 동시에 돌면 같은 캔들을 두 번 받아오게 된다."""
    store = make_lock_store()

    first = await store.claim_lock(tracker.LOCK_NAME, ttl=60)
    second = await store.claim_lock(tracker.LOCK_NAME, ttl=60)

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_lock_can_be_retaken_after_release():
    store = make_lock_store()

    token = await store.claim_lock(tracker.LOCK_NAME, ttl=60)
    assert await store.release_lock(tracker.LOCK_NAME, token) is True
    assert await store.claim_lock(tracker.LOCK_NAME, ttl=60) is not None


@pytest.mark.asyncio
async def test_releasing_someone_elses_lock_does_nothing():
    """분산 락에서 가장 흔한 버그다 — TTL 만료 뒤 남의 락을 지워버린다."""
    store = make_lock_store()
    await store.claim_lock(tracker.LOCK_NAME, ttl=60)

    assert await store.release_lock(tracker.LOCK_NAME, "남의-토큰") is False
    # 원래 주인의 락은 그대로 남아 있어야 한다.
    assert await store.claim_lock(tracker.LOCK_NAME, ttl=60) is None


@pytest.mark.asyncio
async def test_skips_the_cycle_when_another_instance_is_running():
    store = make_lock_store()
    await store.claim_lock(tracker.LOCK_NAME, ttl=60)  # 남이 잡고 있다

    # DB 를 부르지 않고 바로 0 을 돌려줘야 한다.
    assert await tracker.run_once_locked(store, ttl=60) == 0
