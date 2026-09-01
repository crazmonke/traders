"""Upbit WebSocket 수집기.

체결(ticker)·호가(orderbook)를 구독해 Redis에 캐싱하고, 등록된 핸들러로 넘긴다.
연결이 끊기면 지수 백오프로 재접속하며 시도/실패/성공을 모두 로그로 남긴다.
(prompt.md [Step 1] 요구사항 1·3·6)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from trading_engine.config import settings
from trading_engine.market.redis_store import RedisStore
from trading_engine.market.tick_buffer import TickBuffer

log = logging.getLogger(__name__)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

# 재접속 백오프: 1s 에서 시작해 2배씩, 최대 60s
BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 60.0
BACKOFF_FACTOR = 2.0

Handler = Callable[[str, str, dict[str, Any]], Awaitable[None]]
"""(event_type, market, payload) 를 받는 비동기 핸들러."""


def build_subscribe_message(markets: list[str]) -> str:
    """Upbit 구독 페이로드. ticket → type... → format 순서를 지켜야 한다."""
    return json.dumps(
        [
            {"ticket": str(uuid.uuid4())},
            {"type": "ticker", "codes": markets},
            {"type": "orderbook", "codes": markets},
            {"format": "DEFAULT"},
        ]
    )


def parse_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    """Redis에 저장할 형태로 체결 데이터를 추린다."""
    return {
        "market": raw.get("code"),
        "trade_price": raw.get("trade_price"),
        "trade_volume": raw.get("trade_volume"),
        "change_rate": raw.get("signed_change_rate"),
        "acc_trade_volume_24h": raw.get("acc_trade_volume_24h"),
        "acc_trade_price_24h": raw.get("acc_trade_price_24h"),
        "high_price": raw.get("high_price"),
        "low_price": raw.get("low_price"),
        "timestamp": raw.get("timestamp"),
    }


def parse_orderbook(raw: dict[str, Any]) -> dict[str, Any]:
    """호가 잔량 총합과 최우선 호가만 남긴다. 잔량 비율 계산은 Step 1-b(indicators)에서 한다."""
    units = raw.get("orderbook_units") or []
    best = units[0] if units else {}
    return {
        "market": raw.get("code"),
        "total_ask_size": raw.get("total_ask_size"),
        "total_bid_size": raw.get("total_bid_size"),
        "best_ask_price": best.get("ask_price"),
        "best_bid_price": best.get("bid_price"),
        "units": units[:5],
        "timestamp": raw.get("timestamp"),
    }


class UpbitWebSocketClient:
    def __init__(
        self,
        store: RedisStore,
        markets: list[str] | None = None,
        buffer: TickBuffer | None = None,
    ) -> None:
        self._store = store
        self._markets = markets or settings.markets
        self.buffer = buffer or TickBuffer()
        self._handlers: list[Handler] = []
        self._stopping = asyncio.Event()

    def on_event(self, handler: Handler) -> None:
        """지표 계산·룰 엔진 평가를 붙이는 지점 (Step 1-b, Step 2)."""
        self._handlers.append(handler)

    def stop(self) -> None:
        self._stopping.set()

    async def run_forever(self) -> None:
        """끊겨도 지수 백오프로 계속 재접속한다."""
        backoff = BACKOFF_INITIAL_SEC
        attempt = 0

        while not self._stopping.is_set():
            attempt += 1
            try:
                log.info("Upbit WebSocket 접속 시도 (attempt=%d)", attempt)
                await self._consume()
                # 정상 종료(stop 호출)면 루프를 빠져나간다.
                if self._stopping.is_set():
                    break
                log.warning("Upbit WebSocket 스트림이 종료됨 - 재접속한다")
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError) as exc:
                log.warning("Upbit WebSocket 연결 실패 (attempt=%d): %s", attempt, exc)
            except Exception:
                log.exception("Upbit WebSocket 처리 중 예기치 못한 오류 (attempt=%d)", attempt)

            if self._stopping.is_set():
                break

            # 동시 재접속이 몰리지 않도록 지터를 섞는다.
            delay = min(backoff, BACKOFF_MAX_SEC) * (0.5 + random.random() * 0.5)
            log.info("%.1f초 후 재접속한다", delay)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                break  # 대기 중 stop 되면 종료
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX_SEC)

        log.info("Upbit WebSocket 수집을 종료한다")

    async def _consume(self) -> None:
        async with websockets.connect(
            UPBIT_WS_URL, ping_interval=20, ping_timeout=10, max_queue=1024
        ) as ws:
            await ws.send(build_subscribe_message(self._markets))
            log.info("Upbit WebSocket 접속 성공 - 구독 마켓: %s", ", ".join(self._markets))

            async for message in ws:
                if self._stopping.is_set():
                    break
                await self._dispatch(message)

    async def _dispatch(self, message: str | bytes) -> None:
        try:
            raw = json.loads(message)
        except json.JSONDecodeError:
            log.warning("JSON 파싱 실패 - 메시지를 버린다")
            return

        event_type = raw.get("type")
        market = raw.get("code")
        if not market:
            return

        if event_type == "ticker":
            payload = parse_ticker(raw)
            self.buffer.push(market, payload)
            await self._store.save_ticker(market, payload)
        elif event_type == "orderbook":
            payload = parse_orderbook(raw)
            await self._store.save_orderbook(market, payload)
        else:
            return

        for handler in self._handlers:
            try:
                await handler(event_type, market, payload)
            except Exception:
                log.exception("핸들러 실행 실패 (type=%s, market=%s)", event_type, market)
