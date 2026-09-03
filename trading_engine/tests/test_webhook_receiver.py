"""Step 3-b 단위 테스트 — 웹훅 수신 · 유저 격리 · 수신 제한.

DB 는 붙이지 않는다. `store` 모듈을 가짜로 갈아끼워 "무엇을 어떤 인자로 저장하는가"만 본다.
유저 격리는 이 기능의 요구사항 자체이므로(prompt.md v2 §5) 별도 절에서 확인한다.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_engine.external.tradingview import store as real_store
from trading_engine.external.tradingview.ratelimit import RateLimiter, window_key
from trading_engine.external.tradingview.receiver import (
    NOT_FOUND_MESSAGE,
    TOKEN_PATTERN,
    WebhookReceiver,
)
from trading_engine.market.redis_store import RedisStore

TOKEN_A = "A" * 43
TOKEN_B = "B" * 43
REVOKED = "C" * 43


class FakeStore:
    """`store` 모듈 대역. 살아 있는 토큰만 인식하고 저장 내역을 기록한다."""

    ACTIONS = real_store.ACTIONS

    def __init__(self, webhooks=None, linked_signal_id=None):
        self.webhooks = webhooks or {
            TOKEN_A: {"id": 10, "user_id": 1, "webhook_token": TOKEN_A},
            TOKEN_B: {"id": 20, "user_id": 2, "webhook_token": TOKEN_B},
        }
        self.linked_signal_id = linked_signal_id
        self.saved = []
        self.touched = []
        self.link_queries = []

    async def find_active_webhook(self, token):
        return self.webhooks.get(token)

    async def find_linked_signal(self, symbol, window_minutes):
        self.link_queries.append((symbol, window_minutes))
        return self.linked_signal_id

    async def save_signal(self, **kwargs):
        self.saved.append(kwargs)
        return 100 + len(self.saved)

    async def touch_webhook(self, webhook_id):
        self.touched.append(webhook_id)

    def rows_for_user(self, user_id):
        """대시보드가 보여줄 목록. `user_id` 로 거른다."""
        return [row for row in self.saved if row["user_id"] == user_id]


def make_client(store=None, limiter=None, link_window_min=15):
    store = store or FakeStore()
    app = FastAPI()
    app.include_router(
        WebhookReceiver(
            store_module=store, limiter=limiter, link_window_min=link_window_min
        ).router()
    )
    return TestClient(app), store


ALERT = {"symbol": "BTC", "action": "BUY", "strategy_name": "5분봉 돌파"}


# --- 토큰 판정 ---------------------------------------------------------------


def test_token_pattern_matches_the_php_side_format():
    """PHP `WebhookToken` 이 만드는 43자 base64url 과 같은 형식이어야 한다."""
    import base64
    import os

    php_style = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    assert TOKEN_PATTERN.match(php_style)
    assert not TOKEN_PATTERN.match("A" * 42)
    assert not TOKEN_PATTERN.match("A" * 44)
    assert not TOKEN_PATTERN.match("A" * 42 + "+")


def test_unknown_and_malformed_tokens_look_identical():
    """구분해 알려주면 토큰을 긁는 쪽에 형식 정보를 준다."""
    client, _ = make_client()

    unknown = client.post("/webhook/tv/" + "Z" * 43, json=ALERT)
    malformed = client.post("/webhook/tv/short", json=ALERT)

    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json() == malformed.json() == {"error": NOT_FOUND_MESSAGE}


def test_revoked_token_is_rejected():
    """PHP 쪽에서 폐기하면 즉시 안 받아야 한다."""
    client, store = make_client(FakeStore(webhooks={}))

    response = client.post(f"/webhook/tv/{REVOKED}", json=ALERT)

    assert response.status_code == 404
    assert store.saved == []


def test_malformed_token_does_not_touch_the_database():
    client, store = make_client()

    client.post("/webhook/tv/nope", json=ALERT)

    assert store.link_queries == []
    assert store.saved == []


# --- 본문 처리 ---------------------------------------------------------------


def test_minimal_payload_is_accepted():
    """§Step 3 요구사항 4 — symbol 과 action 만 강제한다."""
    client, store = make_client()

    response = client.post(f"/webhook/tv/{TOKEN_A}", json={"symbol": "eth", "action": "sell"})

    assert response.status_code == 201
    saved = store.saved[0]
    assert saved["symbol"] == "ETH"  # 대문자로 정규화
    assert saved["action"] == "SELL"
    assert saved["strategy_name"] is None


def test_unknown_fields_are_kept_in_the_raw_payload():
    """유저마다 Pine Script 가 다르다. 모르는 필드도 잃지 않아야 한다."""
    client, store = make_client()
    alert = {**ALERT, "price": 61420.5, "time": "2026-09-03T10:00:00Z", "내맘대로": [1, 2]}

    client.post(f"/webhook/tv/{TOKEN_A}", json=alert)

    assert store.saved[0]["raw_payload"] == alert
    assert store.saved[0]["strategy_name"] == "5분봉 돌파"


@pytest.mark.parametrize(
    "body",
    [
        {"action": "BUY"},  # symbol 없음
        {"symbol": "BTC"},  # action 없음
        {"symbol": "BTC", "action": "HOLD"},  # ENUM 밖
        {"symbol": "", "action": "BUY"},
        {"symbol": 123, "action": "BUY"},
    ],
)
def test_invalid_bodies_are_rejected(body):
    client, store = make_client()

    assert client.post(f"/webhook/tv/{TOKEN_A}", json=body).status_code == 400
    assert store.saved == []


def test_non_json_body_is_rejected():
    client, _ = make_client()

    response = client.post(
        f"/webhook/tv/{TOKEN_A}",
        content=b"symbol=BTC",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400


def test_oversized_body_is_rejected():
    client, store = make_client()
    huge = {"symbol": "BTC", "action": "BUY", "note": "x" * 20_000}

    assert client.post(f"/webhook/tv/{TOKEN_A}", json=huge).status_code == 400
    assert store.saved == []


def test_long_symbol_is_truncated_to_the_column_width():
    """external_signals.symbol 이 VARCHAR(20). 넘치면 INSERT 가 실패한다."""
    client, store = make_client()

    client.post(f"/webhook/tv/{TOKEN_A}", json={"symbol": "X" * 40, "action": "BUY"})

    assert len(store.saved[0]["symbol"]) == 20


# --- 신호 연결 ---------------------------------------------------------------


def test_recent_signal_is_linked_for_reference_only():
    client, store = make_client(FakeStore(linked_signal_id=777), link_window_min=15)

    response = client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)

    assert response.json()["linked_signal_id"] == 777
    assert store.saved[0]["linked_signal_id"] == 777
    assert store.link_queries == [("BTC", 15)]


def test_missing_recent_signal_is_not_an_error():
    """우리 신호가 없어도 유저 알림은 받아야 한다."""
    client, store = make_client(FakeStore(linked_signal_id=None))

    response = client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)

    assert response.status_code == 201
    assert store.saved[0]["linked_signal_id"] is None


def test_last_received_at_is_updated():
    client, store = make_client()

    client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)

    assert store.touched == [10]


# --- 유저 격리 (Step 3 DoD) --------------------------------------------------


def test_signals_are_stored_under_the_token_owner():
    client, store = make_client()

    client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)
    client.post(f"/webhook/tv/{TOKEN_B}", json={"symbol": "SOL", "action": "SELL"})

    assert store.saved[0]["user_id"] == 1
    assert store.saved[0]["user_webhook_id"] == 10
    assert store.saved[1]["user_id"] == 2
    assert store.saved[1]["user_webhook_id"] == 20


def test_user_a_signal_never_appears_for_user_b():
    """prompt.md v2 [Step 3] 요구사항 8 — 대시보드에 섞이면 안 된다."""
    client, store = make_client()

    client.post(f"/webhook/tv/{TOKEN_A}", json={"symbol": "BTC", "action": "BUY"})
    client.post(f"/webhook/tv/{TOKEN_A}", json={"symbol": "ETH", "action": "SELL"})
    client.post(f"/webhook/tv/{TOKEN_B}", json={"symbol": "SOL", "action": "EXIT"})

    a_rows = store.rows_for_user(1)
    b_rows = store.rows_for_user(2)

    assert [row["symbol"] for row in a_rows] == ["BTC", "ETH"]
    assert [row["symbol"] for row in b_rows] == ["SOL"]
    assert all(row["user_id"] == 2 for row in b_rows)
    # 유저 B 의 목록에 유저 A 의 심볼이 하나도 없어야 한다
    assert not set(r["symbol"] for r in b_rows) & set(r["symbol"] for r in a_rows)


def test_list_for_user_query_is_scoped_by_user():
    """SQL 에서 user_id 조건이 빠지면 남의 전략이 노출된다."""
    import inspect

    source = inspect.getsource(real_store.list_for_user)
    assert "WHERE user_id = %s" in source


# --- 수신 제한 ---------------------------------------------------------------


class _FakeRedis:
    def __init__(self, broken=False):
        self.counts = {}
        self.broken = broken

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.key = None

    def incr(self, key):
        self.key = key

    def expire(self, key, ttl):
        pass

    async def execute(self):
        if self.redis.broken:
            raise ConnectionError("redis down")
        self.redis.counts[self.key] = self.redis.counts.get(self.key, 0) + 1
        return [self.redis.counts[self.key], True]


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_the_limit():
    limiter = RateLimiter(RedisStore(client=_FakeRedis()), limit_per_min=3)

    results = [await limiter.allow(TOKEN_A) for _ in range(5)]

    assert results == [True, True, True, False, False]


@pytest.mark.asyncio
async def test_rate_limit_is_per_token():
    limiter = RateLimiter(RedisStore(client=_FakeRedis()), limit_per_min=1)

    assert await limiter.allow(TOKEN_A) is True
    assert await limiter.allow(TOKEN_A) is False
    assert await limiter.allow(TOKEN_B) is True


@pytest.mark.asyncio
async def test_rate_limiter_lets_traffic_through_when_redis_is_down():
    """유실보다 초과 허용이 낫다 - 뒤에 비싼 작업이 없다."""
    limiter = RateLimiter(RedisStore(client=_FakeRedis(broken=True)), limit_per_min=1)

    assert await limiter.allow(TOKEN_A) is True


def test_rate_window_key_rolls_over_every_minute():
    assert window_key(TOKEN_A, now=0.0) != window_key(TOKEN_A, now=60.0)
    assert window_key(TOKEN_A, now=0.0) == window_key(TOKEN_A, now=59.9)


def test_receiver_returns_429_when_limited():
    limiter = RateLimiter(RedisStore(client=_FakeRedis()), limit_per_min=1)
    client, store = make_client(limiter=limiter)

    first = client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)
    second = client.post(f"/webhook/tv/{TOKEN_A}", json=ALERT)

    assert first.status_code == 201
    assert second.status_code == 429
    assert len(store.saved) == 1


# --- 앱 구성 -----------------------------------------------------------------


def test_app_exposes_nothing_but_the_receiver():
    """공개 주소다. 스키마·문서를 노출하지 않는다."""
    from trading_engine.external.tradingview.receiver import create_app

    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/webhook/tv/{token}" in paths
    assert app.docs_url is None and app.openapi_url is None

    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_only_post_is_accepted():
    client, _ = make_client()

    assert client.get(f"/webhook/tv/{TOKEN_A}").status_code == 405
    assert client.delete(f"/webhook/tv/{TOKEN_A}").status_code == 405
