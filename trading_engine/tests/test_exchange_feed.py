"""Step 1-a 다중 거래소 어댑터 단위 테스트."""

import asyncio
import json

import pytest

from trading_engine.market.exchange_feed import (
    BACKOFF_INITIAL_SEC,
    ExchangeFeed,
    normalize_orderbook,
    normalize_ticker,
)
from trading_engine.market.exchange_registry import (
    REGISTRY,
    ExchangeSpec,
    UnknownExchangeError,
    get_spec,
    resolve_specs,
)
from trading_engine.market.redis_store import RedisStore, exchange_key

BINANCE = get_spec("binance")
UPBIT = get_spec("upbit")


class _FakeRedis:
    def __init__(self):
        self.strings = {}

    async def set(self, key, value, ex=None):
        self.strings[key] = value


def make_store():
    return RedisStore(client=_FakeRedis())


# --- 레지스트리 ---------------------------------------------------------------


def test_registry_covers_the_five_exchanges_seeded_in_db():
    assert set(REGISTRY) == {"binance", "okx", "bybit", "coinbase", "upbit"}


def test_only_upbit_is_a_private_trading_target():
    targets = [code for code, spec in REGISTRY.items() if spec.is_private_trading_target]
    assert targets == ["upbit"]


def test_symbol_uses_each_exchange_quote_currency():
    assert BINANCE.symbol("BTC") == "BTC/USDT"
    assert UPBIT.symbol("BTC") == "BTC/KRW"
    assert get_spec("coinbase").symbol("DOGE") == "DOGE/USD"


def test_resolve_specs_rejects_unknown_code_before_collecting():
    with pytest.raises(UnknownExchangeError):
        resolve_specs(["binance", "오타거래소"])


# --- 정규화 -------------------------------------------------------------------


def test_normalize_ticker_maps_ccxt_fields():
    payload = normalize_ticker(
        BINANCE,
        {"symbol": "BTC/USDT", "last": 77285.0, "percentage": 1.5,
         "baseVolume": 12.0, "quoteVolume": 900000.0, "high": 78000.0,
         "low": 76000.0, "timestamp": 1_788_000_000_000},
    )

    assert payload["exchange"] == "binance"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["trade_price"] == 77285.0
    assert payload["timestamp"] == 1_788_000_000_000
    # 24시간 누적 거래량을 봉 거래량으로 오해하지 않도록 trade_volume 은 두지 않는다
    assert "trade_volume" not in payload


def test_normalize_orderbook_sums_only_top_depth_levels():
    book = {
        "symbol": "BTC/USDT",
        "bids": [[100.0, 1.0], [99.0, 2.0], [98.0, 4.0]],
        "asks": [[101.0, 1.0], [102.0, 1.0], [103.0, 8.0]],
        "timestamp": 1,
    }

    payload = normalize_orderbook(BINANCE, book, depth=2)

    assert payload["total_bid_size"] == 3.0  # 1 + 2, 3단째 제외
    assert payload["total_ask_size"] == 2.0
    assert payload["best_bid_price"] == 100.0
    assert payload["best_ask_price"] == 101.0
    assert payload["depth"] == 2


def test_normalize_orderbook_tolerates_empty_sides():
    payload = normalize_orderbook(BINANCE, {"symbol": "BTC/USDT", "bids": [], "asks": []})

    assert payload["total_bid_size"] == 0.0
    assert payload["best_bid_price"] is None


def test_orderbook_depth_makes_imbalance_comparable_across_exchanges():
    """호가 단수가 다른 두 거래소가 같은 상위 depth 로 잘려 같은 값을 낸다."""
    shallow = {"symbol": "BTC/USDT", "bids": [[100, 3.0]], "asks": [[101, 1.0]]}
    deep = {
        "symbol": "BTC/USDT",
        "bids": [[100, 3.0]] + [[90 - i, 100.0] for i in range(50)],
        "asks": [[101, 1.0]] + [[110 + i, 100.0] for i in range(50)],
    }

    a = normalize_orderbook(BINANCE, shallow, depth=1)
    b = normalize_orderbook(BINANCE, deep, depth=1)

    assert (a["total_bid_size"], a["total_ask_size"]) == (b["total_bid_size"], b["total_ask_size"])


# --- 수집 루프 ----------------------------------------------------------------


class _FakeClient:
    """watch_* 를 정해진 횟수만 돌려주고 그 뒤 멈추는 가짜 ccxt 클라이언트."""

    def __init__(self, ticks=1, fail_on_load=False):
        self._ticks = ticks
        self._fail_on_load = fail_on_load
        self.load_markets_called = 0
        self.closed = False

    async def load_markets(self):
        self.load_markets_called += 1
        if self._fail_on_load:
            raise RuntimeError("접속 실패")

    async def watch_ticker(self, symbol):
        if self._ticks <= 0:
            await asyncio.sleep(3600)
        self._ticks -= 1
        return {"symbol": symbol, "last": 100.0, "timestamp": 1}

    async def watch_order_book(self, symbol):
        await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


async def run_briefly(feed, seconds=0.05):
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(seconds)
    feed.stop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_feed_loads_markets_before_watching():
    """OKX 는 load_markets 없이 watch_ticker 하면 실패한다."""
    client = _FakeClient(ticks=1)
    store = make_store()
    feed = ExchangeFeed(get_spec("okx"), store, ["BTC"], client_factory=lambda spec: client)

    await run_briefly(feed)

    assert client.load_markets_called == 1


@pytest.mark.asyncio
async def test_feed_caches_ticker_under_exchange_scoped_key():
    client = _FakeClient(ticks=1)
    fake = _FakeRedis()
    feed = ExchangeFeed(
        BINANCE, RedisStore(client=fake), ["BTC"], client_factory=lambda spec: client
    )

    await run_briefly(feed)

    key = exchange_key("binance", "BTC/USDT", "ticker")
    assert key == "exchange:binance:BTC-USDT:ticker"
    assert json.loads(fake.strings[key])["trade_price"] == 100.0


@pytest.mark.asyncio
async def test_feed_dispatches_events_with_exchange_code():
    client = _FakeClient(ticks=1)
    feed = ExchangeFeed(
        BINANCE, make_store(), ["BTC"], client_factory=lambda spec: client
    )
    seen = []

    async def handler(event_type, exchange, symbol, payload):
        seen.append((event_type, exchange, symbol))

    feed.on_event(handler)
    await run_briefly(feed)

    assert ("ticker", "binance", "BTC/USDT") in seen


@pytest.mark.asyncio
async def test_feed_survives_handler_failure():
    client = _FakeClient(ticks=1)
    feed = ExchangeFeed(BINANCE, make_store(), ["BTC"], client_factory=lambda spec: client)
    ok = []

    async def boom(*args):
        raise RuntimeError("핸들러 폭발")

    async def fine(event_type, exchange, symbol, payload):
        ok.append(exchange)

    feed.on_event(boom)
    feed.on_event(fine)
    await run_briefly(feed)

    assert ok == ["binance"]


@pytest.mark.asyncio
async def test_feed_retries_and_closes_client_after_connection_failure():
    """접속이 실패해도 예외를 밖으로 던지지 않고 재시도하며, 클라이언트를 닫는다."""
    clients = []

    def factory(spec):
        client = _FakeClient(fail_on_load=True)
        clients.append(client)
        return client

    feed = ExchangeFeed(BINANCE, make_store(), ["BTC"], client_factory=factory)

    await run_briefly(feed, seconds=0.05)

    assert clients, "클라이언트가 만들어지지 않았다"
    assert all(client.closed for client in clients), "실패한 커넥션이 정리되지 않았다"


@pytest.mark.asyncio
async def test_one_exchange_failure_does_not_stop_another():
    """prompt.md v2 [Step 1] 요구사항 4 — 거래소 간 장애 격리."""
    fake = _FakeRedis()
    store = RedisStore(client=fake)
    broken = ExchangeFeed(
        get_spec("okx"), store, ["BTC"], client_factory=lambda s: _FakeClient(fail_on_load=True)
    )
    healthy = ExchangeFeed(
        BINANCE, store, ["BTC"], client_factory=lambda s: _FakeClient(ticks=1)
    )

    tasks = [asyncio.create_task(broken.run()), asyncio.create_task(healthy.run())]
    await asyncio.sleep(0.05)
    broken.stop()
    healthy.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # 죽은 okx 와 무관하게 binance 는 수집됐다
    assert exchange_key("binance", "BTC/USDT", "ticker") in fake.strings
    assert exchange_key("okx", "BTC/USDT", "ticker") not in fake.strings


@pytest.mark.asyncio
async def test_feed_skips_orderbook_when_exchange_does_not_support_it():
    spec = ExchangeSpec("nobook", "NoBook", "USDT", supports_orderbook=False)
    client = _FakeClient(ticks=1)
    feed = ExchangeFeed(spec, make_store(), ["BTC"], client_factory=lambda s: client)

    watched = []
    original = client.watch_order_book

    async def spy(symbol):
        watched.append(symbol)
        return await original(symbol)

    client.watch_order_book = spy
    await run_briefly(feed)

    assert watched == []


def test_backoff_starts_at_one_second():
    assert BACKOFF_INITIAL_SEC == 1.0
