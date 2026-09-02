"""거래소×심볼 단위 지표 파이프라인.

캔들/호가 이벤트 → 지표 계산 → Redis 저장 → 콜백.
콜백 자리에 Step 2 의 Consensus·RuleEngine 평가가 붙는다.
(prompt.md v2 [Step 1] 요구사항 5·7)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from trading_engine.indicators import calculator
from trading_engine.indicators.calculator import Indicators
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# 한 거래소×심볼에 대해 이 간격보다 자주 다시 계산하지 않는다.
RECALC_MIN_INTERVAL_SEC = 1.0

IndicatorHandler = Callable[[str, str, Indicators], Awaitable[None]]
"""(exchange, symbol, indicators) 를 받는 비동기 핸들러."""


class IndicatorEngine:
    """ExchangeFeed 의 이벤트를 받아 거래소별 지표를 낸다."""

    def __init__(
        self,
        store: RedisStore,
        min_interval_sec: float = RECALC_MIN_INTERVAL_SEC,
    ) -> None:
        self._store = store
        self._min_interval = min_interval_sec
        self._candles: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._orderbooks: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_calc: dict[tuple[str, str], float] = {}
        self._handlers: list[IndicatorHandler] = []
        self.latest: dict[tuple[str, str], Indicators] = {}

    def on_indicators(self, handler: IndicatorHandler) -> None:
        """지표가 갱신될 때마다 호출된다 (Step 2 진입점)."""
        self._handlers.append(handler)

    async def handle_event(
        self, event_type: str, exchange: str, symbol: str, payload: dict[str, Any]
    ) -> None:
        """ExchangeFeed.on_event 에 그대로 넘길 핸들러."""
        key = (exchange, symbol)
        if event_type == "candle":
            candles = payload.get("candles")
            if not candles:
                return
            self._candles[key] = candles
            # 봉이 갱신되면 스로틀을 무시하고 바로 계산한다.
            await self.recalculate(exchange, symbol)
            return

        if event_type == "orderbook":
            self._orderbooks[key] = payload
            if self._should_recalculate(key):
                await self.recalculate(exchange, symbol)
        # ticker 는 캔들에 이미 반영되므로 재계산 트리거로 쓰지 않는다.

    def _should_recalculate(self, key: tuple[str, str]) -> bool:
        now = time.monotonic()
        if now - self._last_calc.get(key, 0.0) >= self._min_interval:
            self._last_calc[key] = now
            return True
        return False

    async def recalculate(self, exchange: str, symbol: str) -> Indicators | None:
        key = (exchange, symbol)
        candles = self._candles.get(key)
        if not candles:
            return None

        try:
            indicators = calculator.compute(symbol, candles, self._orderbooks.get(key))
        except Exception:
            log.exception(
                "지표 계산 실패 (exchange=%s, symbol=%s, candles=%d)",
                exchange,
                symbol,
                len(candles),
            )
            return None

        self._last_calc[key] = time.monotonic()
        self.latest[key] = indicators
        try:
            await self._store.save_exchange_indicators(exchange, symbol, indicators.as_dict())
        except Exception:
            log.exception("지표 캐싱 실패 (exchange=%s, symbol=%s)", exchange, symbol)

        for handler in self._handlers:
            try:
                await handler(exchange, symbol, indicators)
            except Exception:
                log.exception(
                    "지표 핸들러 실행 실패 (exchange=%s, symbol=%s)", exchange, symbol
                )

        return indicators
