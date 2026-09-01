"""Step 1-a 수집 계층 단위 테스트."""

import json

import pytest

from trading_engine.market.tick_buffer import TickBuffer
from trading_engine.market.redis_store import RedisStore, orderbook_key, ticker_key
from trading_engine.market.upbit_ws import (
    build_subscribe_message,
    parse_orderbook,
    parse_ticker,
)

MARKETS = ["KRW-BTC", "KRW-ETH"]


def test_subscribe_message_has_ticket_and_both_types():
    payload = json.loads(build_subscribe_message(MARKETS))

    assert "ticket" in payload[0]
    types = {item.get("type") for item in payload if "type" in item}
    assert types == {"ticker", "orderbook"}
    assert all(item["codes"] == MARKETS for item in payload if "codes" in item)


def test_parse_ticker_extracts_price_fields():
    parsed = parse_ticker(
        {"type": "ticker", "code": "KRW-BTC", "trade_price": 154200000,
         "signed_change_rate": 0.0123, "timestamp": 1}
    )

    assert parsed["market"] == "KRW-BTC"
    assert parsed["trade_price"] == 154200000
    assert parsed["change_rate"] == 0.0123


def test_parse_orderbook_keeps_totals_and_top_units():
    units = [{"ask_price": i, "bid_price": i, "ask_size": 1, "bid_size": 2} for i in range(15)]
    parsed = parse_orderbook(
        {"code": "KRW-BTC", "total_ask_size": 10.5, "total_bid_size": 12.5,
         "orderbook_units": units}
    )

    assert parsed["total_ask_size"] == 10.5
    assert parsed["total_bid_size"] == 12.5
    assert parsed["best_ask_price"] == 0
    assert len(parsed["units"]) == 5


def test_parse_orderbook_tolerates_empty_units():
    parsed = parse_orderbook({"code": "KRW-BTC", "orderbook_units": []})

    assert parsed["best_ask_price"] is None
    assert parsed["units"] == []


def test_tick_buffer_drops_oldest_beyond_maxlen():
    buf = TickBuffer(maxlen=3)
    for price in range(5):
        buf.push("KRW-BTC", {"trade_price": price})

    prices = [tick["trade_price"] for tick in buf.recent("KRW-BTC")]
    assert prices == [2, 3, 4]
    assert len(buf) == 3


def test_tick_buffer_isolates_markets():
    buf = TickBuffer()
    buf.push("KRW-BTC", {"trade_price": 1})
    buf.push("KRW-ETH", {"trade_price": 2})

    assert buf.recent("KRW-BTC") == [{"trade_price": 1}]
    assert buf.recent("KRW-XRP") == []


def test_redis_keys_follow_spec():
    assert ticker_key("KRW-BTC") == "market:KRW-BTC:ticker"
    assert orderbook_key("KRW-BTC") == "market:KRW-BTC:orderbook"


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = (value, ex)


@pytest.mark.asyncio
async def test_save_ticker_writes_json_with_ttl():
    fake = _FakeRedis()
    store = RedisStore(client=fake, ttl=60)

    await store.save_ticker("KRW-BTC", {"trade_price": 100})

    value, ttl = fake.store["market:KRW-BTC:ticker"]
    assert json.loads(value) == {"trade_price": 100}
    assert ttl == 60
