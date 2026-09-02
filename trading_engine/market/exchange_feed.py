"""거래소 하나를 담당하는 수집 태스크.

거래소마다 독립된 커넥션과 백오프를 갖는다. 한 거래소가 죽어도 다른 거래소
수집은 멈추지 않는다. (prompt.md v2 [Step 1] 요구사항 3·4)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from trading_engine.market.exchange_registry import ExchangeSpec, create_client
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# 재접속 백오프: 1s 에서 시작해 2배씩, 최대 60s. upbit_ws 와 같은 정책.
BACKOFF_INITIAL_SEC = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_MAX_SEC = 60.0

Handler = Callable[[str, str, str, dict[str, Any]], Awaitable[None]]
"""(event_type, exchange_code, symbol, payload) 를 받는 비동기 핸들러."""


def normalize_ticker(spec: ExchangeSpec, ticker: dict[str, Any]) -> dict[str, Any]:
    """ccxt ticker 를 거래소 무관 형태로 정리한다.

    `trade_volume` 은 두지 않는다. ccxt ticker 의 baseVolume 은 24시간 누적이라
    체결 단위 거래량이 아니고, 이걸 봉에 더하면 거래량이 폭주한다.
    봉 거래량은 Step 1-b 에서 OHLCV 로 받는다.
    """
    return {
        "exchange": spec.code,
        "symbol": ticker.get("symbol"),
        "trade_price": ticker.get("last"),
        "change_rate": ticker.get("percentage"),
        "base_volume_24h": ticker.get("baseVolume"),
        "quote_volume_24h": ticker.get("quoteVolume"),
        "high_price": ticker.get("high"),
        "low_price": ticker.get("low"),
        "timestamp": ticker.get("timestamp"),
    }


def _side_total(levels: Any, depth: int) -> float:
    """[[price, amount], ...] 상위 depth 단계 잔량 합계."""
    total = 0.0
    for level in (levels or [])[:depth]:
        if len(level) >= 2 and level[1] is not None:
            total += float(level[1])
    return total


def normalize_orderbook(
    spec: ExchangeSpec, orderbook: dict[str, Any], depth: int = 15
) -> dict[str, Any]:
    """ccxt orderbook 을 잔량 총합 + 최우선 호가로 줄인다.

    거래소마다 내려주는 호가 단수가 크게 다르다(bybit 50 vs coinbase 2만+).
    그대로 합치면 호가 불균형이 거래소별로 다른 뜻이 되므로 상위 depth 단만 쓴다.
    """
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    return {
        "exchange": spec.code,
        "symbol": orderbook.get("symbol"),
        "total_bid_size": _side_total(bids, depth),
        "total_ask_size": _side_total(asks, depth),
        "best_bid_price": bids[0][0] if bids else None,
        "best_ask_price": asks[0][0] if asks else None,
        "depth": depth,
        "timestamp": orderbook.get("timestamp"),
    }


class ExchangeFeed:
    """한 거래소의 ticker·orderbook 을 구독해 Redis 에 넣고 핸들러를 호출한다."""

    def __init__(
        self,
        spec: ExchangeSpec,
        store: RedisStore,
        bases: list[str],
        client_factory: Callable[[ExchangeSpec], Any] = create_client,
        orderbook_depth: int = 15,
    ) -> None:
        self._spec = spec
        self._store = store
        self._bases = list(bases)
        self._client_factory = client_factory
        self._depth = orderbook_depth
        self._handlers: list[Handler] = []
        self._stopping = asyncio.Event()

    @property
    def code(self) -> str:
        return self._spec.code

    def on_event(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """끊겨도 지수 백오프로 계속 재접속한다. 예외를 밖으로 던지지 않는다."""
        backoff = BACKOFF_INITIAL_SEC
        while not self._stopping.is_set():
            client = None
            try:
                client = self._client_factory(self._spec)
                # OKX 는 markets 를 먼저 읽지 않으면 watch_ticker 가
                # "markets not loaded" 로 실패한다. 2026-09-02 실측 확인.
                await client.load_markets()
                log.info("%s 접속 성공 - 구독 심볼: %s", self.code, ", ".join(self._bases))
                backoff = BACKOFF_INITIAL_SEC
                await self._consume(client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s 수집 중 오류 - 재접속한다", self.code)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        log.debug("%s 클라이언트 정리 실패", self.code, exc_info=True)

            if self._stopping.is_set():
                break
            # 동시 재접속이 몰리지 않도록 지터를 섞는다.
            delay = min(backoff, BACKOFF_MAX_SEC) * (0.5 + random.random() * 0.5)
            log.info("%s %.1f초 후 재접속한다", self.code, delay)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                break  # 대기 중 종료 신호
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX_SEC)

        log.info("%s 수집을 종료한다", self.code)

    async def _consume(self, client: Any) -> None:
        """심볼×채널 별 구독 루프를 동시에 돌린다. 하나가 죽으면 전부 접고 재접속한다."""
        tasks = []
        for base in self._bases:
            symbol = self._spec.symbol(base)
            tasks.append(self._watch_ticker(client, symbol))
            if self._spec.supports_orderbook:
                tasks.append(self._watch_orderbook(client, symbol))
        await asyncio.gather(*tasks)

    async def _watch_ticker(self, client: Any, symbol: str) -> None:
        while not self._stopping.is_set():
            raw = await client.watch_ticker(symbol)
            payload = normalize_ticker(self._spec, raw)
            await self._store.save_exchange_ticker(self.code, symbol, payload)
            await self._dispatch("ticker", symbol, payload)

    async def _watch_orderbook(self, client: Any, symbol: str) -> None:
        while not self._stopping.is_set():
            raw = await client.watch_order_book(symbol)
            payload = normalize_orderbook(self._spec, raw, self._depth)
            await self._store.save_exchange_orderbook(self.code, symbol, payload)
            await self._dispatch("orderbook", symbol, payload)

    async def _dispatch(self, event_type: str, symbol: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers:
            try:
                await handler(event_type, self.code, symbol, payload)
            except Exception:
                log.exception(
                    "핸들러 실행 실패 (exchange=%s, type=%s, symbol=%s)",
                    self.code,
                    event_type,
                    symbol,
                )
