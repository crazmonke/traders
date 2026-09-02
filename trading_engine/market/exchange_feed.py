"""거래소 하나를 담당하는 수집 태스크.

거래소마다 독립된 커넥션과 백오프를 갖는다. 한 거래소가 죽어도 다른 거래소
수집은 멈추지 않는다. (prompt.md v2 [Step 1] 요구사항 3·4)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from ccxt.base.errors import NetworkError, NotSupported

from trading_engine.market.exchange_registry import ExchangeSpec, create_client
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# 재접속 백오프: 1s 에서 시작해 2배씩, 최대 60s. upbit_ws 와 같은 정책.
BACKOFF_INITIAL_SEC = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_MAX_SEC = 60.0

CANDLE_TIMEFRAME = "5m"
CANDLE_LIMIT = 100  # prompt.md v2 [Step 1] 요구사항 5 — 최근 100봉
# watchOHLCV 를 지원하지 않는 거래소(coinbase)는 이 주기로 다시 받아온다.
CANDLE_POLL_SEC = 20.0
# 채널 하나가 일시적으로 끊겼을 때 그 채널만 쉬었다 재시도하는 간격
CHANNEL_RETRY_SEC = 2.0

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


def normalize_ohlcv(rows: Any) -> list[dict[str, Any]]:
    """ccxt OHLCV([ts, o, h, l, c, v]) 를 내부 캔들 표현으로. 오래된 봉부터."""
    candles = []
    for row in rows or []:
        if len(row) < 6 or row[4] is None:
            continue
        candles.append(
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5] or 0.0),
            }
        )
    return candles


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
        # 마켓별 최근 캔들. 지표 계산이 여기서 읽어간다.
        self.candles: dict[str, list[dict[str, Any]]] = {}

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
        # 지표는 캔들이 있어야 나온다. 구독 전에 REST 로 100봉을 채운다.
        for base in self._bases:
            await self._seed_candles(client, self._spec.symbol(base))

        tasks = []
        for base in self._bases:
            symbol = self._spec.symbol(base)
            tasks.append(self._watch_ticker(client, symbol))
            tasks.append(self._follow_candles(client, symbol))
            if self._spec.supports_orderbook:
                tasks.append(self._watch_orderbook(client, symbol))
        await asyncio.gather(*tasks)

    async def _seed_candles(self, client: Any, symbol: str) -> None:
        """실패해도 수집은 계속한다. 봉이 쌓이면 지표도 뒤늦게 나온다."""
        try:
            rows = await client.fetch_ohlcv(symbol, CANDLE_TIMEFRAME, limit=CANDLE_LIMIT)
        except Exception:
            log.exception("%s %s 캔들 시딩 실패 - 이후 갱신으로 채운다", self.code, symbol)
            return
        candles = normalize_ohlcv(rows)
        if candles:
            await self._store_candles(symbol, candles)
            log.info("%s %s 캔들 시딩 완료 (%d봉)", self.code, symbol, len(candles))
            # 여기서 이벤트를 쏘지 않으면 폴링 경로(upbit·coinbase)는 첫 폴링까지
            # 지표가 비어 있다. 시딩한 100봉으로 지표는 이미 낼 수 있다.
            await self._dispatch("candle", symbol, {"symbol": symbol, "candles": candles})

    async def _follow_candles(self, client: Any, symbol: str) -> None:
        """watchOHLCV 로 구독하고, 안 되면 REST 폴링으로 물러선다.

        `has["watchOHLCV"]` 만으로는 판단할 수 없다. upbit 은 이 플래그가 True 지만
        5분봉은 지원하지 않아 NotSupported 를 던진다(2026-09-02 확인). 거래소마다
        지원 봉이 다르므로 플래그 대신 실제로 던지는 예외를 보고 결정한다.
        """
        supports_watch = bool(getattr(client, "has", {}).get("watchOHLCV"))
        while not self._stopping.is_set():
            if supports_watch:
                try:
                    rows = await client.watch_ohlcv(symbol, CANDLE_TIMEFRAME)
                except NotSupported as exc:
                    log.info(
                        "%s %s watchOHLCV(%s) 미지원 - %.0f초 폴링으로 전환한다 (%s)",
                        self.code, symbol, CANDLE_TIMEFRAME, CANDLE_POLL_SEC, exc,
                    )
                    supports_watch = False
                    continue
                candles = self._merge(symbol, normalize_ohlcv(rows))
            else:
                await asyncio.sleep(CANDLE_POLL_SEC)
                rows = await client.fetch_ohlcv(symbol, CANDLE_TIMEFRAME, limit=CANDLE_LIMIT)
                candles = normalize_ohlcv(rows)
            if not candles:
                continue
            await self._store_candles(symbol, candles)
            # 지표 엔진이 캔들을 바로 쓰도록 payload 에 실어 보낸다 (같은 프로세스라 직렬화 없음).
            await self._dispatch("candle", symbol, {"symbol": symbol, "candles": candles})

    def _merge(self, symbol: str, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """watch_ohlcv 는 갱신된 봉만 준다. 기존 봉 위에 덮어쓴다.

        같은 ts 면 교체(진행 중인 봉이 자라는 경우), 새 ts 면 추가.
        """
        merged = {candle["ts"]: candle for candle in self.candles.get(symbol, [])}
        for candle in updates:
            merged[candle["ts"]] = candle
        ordered = [merged[ts] for ts in sorted(merged)]
        return ordered[-CANDLE_LIMIT:]

    async def _store_candles(self, symbol: str, candles: list[dict[str, Any]]) -> None:
        self.candles[symbol] = candles
        await self._store.save_exchange_candles(self.code, symbol, candles, CANDLE_TIMEFRAME)

    async def _tolerate_drops(self, channel: str, symbol: str, step) -> None:
        """채널 하나의 일시적 끊김을 거래소 전체 재접속으로 키우지 않는다.

        거래소는 유휴 연결을 정상 종료(code 1000)로 끊는다. 그 예외가 gather 밖으로
        나가면 같은 거래소의 다른 채널까지 전부 접혔다가 다시 붙는데, 그러면 캔들
        폴링 주기(20초)에 도달하기 전에 계속 리셋돼 지표가 영영 안 나온다.
        2026-09-02 upbit 에서 실제로 발생.

        네트워크 계열이 아닌 예외는 그대로 올려보내 거래소 단위 백오프를 태운다.
        """
        while not self._stopping.is_set():
            try:
                await step()
            except NetworkError as exc:
                log.info(
                    "%s %s %s 채널 일시 끊김 - %.0f초 후 재시도 (%s)",
                    self.code, symbol, channel, CHANNEL_RETRY_SEC, exc,
                )
                await asyncio.sleep(CHANNEL_RETRY_SEC)

    async def _watch_ticker(self, client: Any, symbol: str) -> None:
        async def step() -> None:
            raw = await client.watch_ticker(symbol)
            payload = normalize_ticker(self._spec, raw)
            await self._store.save_exchange_ticker(self.code, symbol, payload)
            await self._dispatch("ticker", symbol, payload)

        await self._tolerate_drops("ticker", symbol, step)

    async def _watch_orderbook(self, client: Any, symbol: str) -> None:
        async def step() -> None:
            raw = await client.watch_order_book(symbol)
            payload = normalize_orderbook(self._spec, raw, self._depth)
            await self._store.save_exchange_orderbook(self.code, symbol, payload)
            await self._dispatch("orderbook", symbol, payload)

        await self._tolerate_drops("orderbook", symbol, step)

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
