"""지표 갱신 이벤트 파이프라인.

WebSocket 이벤트 → 캔들 갱신 → 지표 계산 → Redis 저장 → 등록된 콜백 호출.
콜백 자리에 Step 2의 룰 엔진 평가 함수가 붙는다.
(prompt.md [Step 1] 요구사항 5)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from trading_engine.indicators import calculator
from trading_engine.indicators.calculator import Indicators
from trading_engine.market.candle_feed import CandleFeed
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# 초당 수십 틱이 들어와도 마켓당 이 간격으로만 다시 계산한다.
RECALC_MIN_INTERVAL_SEC = 1.0

IndicatorHandler = Callable[[str, Indicators], Awaitable[None]]
"""(market, indicators) 를 받는 비동기 핸들러."""


class IndicatorEngine:
    def __init__(
        self,
        store: RedisStore,
        feed: CandleFeed,
        min_interval_sec: float = RECALC_MIN_INTERVAL_SEC,
    ) -> None:
        self._store = store
        self._feed = feed
        self._min_interval = min_interval_sec
        self._orderbooks: dict[str, dict[str, Any]] = {}
        self._last_calc: dict[str, float] = {}
        self._handlers: list[IndicatorHandler] = []
        self.latest: dict[str, Indicators] = {}

    def on_indicators(self, handler: IndicatorHandler) -> None:
        """지표가 갱신될 때마다 호출된다 (Step 2 룰 엔진 진입점)."""
        self._handlers.append(handler)

    async def prime(self) -> None:
        """시딩 직후 캔들·지표를 한 번 채운다.

        캔들 저장은 봉이 닫힐 때만 하므로, 이게 없으면 기동 후 최대 5분 동안
        `market:{code}:candles:5m` 가 비어 있게 된다.
        """
        for market in self._feed.markets:
            candles = self._feed.candles(market)
            if not candles:
                continue
            await self._store.save_candles(market, candles, self._feed.interval)
            await self.recalculate(market)

    async def handle_event(self, event_type: str, market: str, payload: dict[str, Any]) -> None:
        """UpbitWebSocketClient.on_event 에 그대로 넘길 핸들러."""
        rolled = False
        if event_type == "ticker":
            _, rolled = self._feed.update_from_tick(market, payload)
            if rolled:
                # 봉이 닫힐 때만 캔들 리스트를 통째로 다시 쓴다.
                await self._store.save_candles(
                    market, self._feed.candles(market), self._feed.interval
                )
        elif event_type == "orderbook":
            self._orderbooks[market] = payload
        else:
            return

        # 봉이 바뀌는 순간은 스로틀을 무시하고 바로 계산한다.
        if not self._should_recalculate(market, force=rolled):
            return
        await self.recalculate(market)

    def _should_recalculate(self, market: str, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self._last_calc.get(market, 0.0) >= self._min_interval:
            self._last_calc[market] = now
            return True
        return False

    async def recalculate(self, market: str) -> Indicators | None:
        candles = self._feed.candles(market)
        if not candles:
            return None

        try:
            indicators = calculator.compute(market, candles, self._orderbooks.get(market))
        except Exception:
            log.exception("지표 계산 실패 (market=%s, candles=%d)", market, len(candles))
            return None

        self.latest[market] = indicators
        try:
            await self._store.save_indicators(market, indicators.as_dict())
        except Exception:
            log.exception("지표 캐싱 실패 (market=%s)", market)

        for handler in self._handlers:
            try:
                await handler(market, indicators)
            except Exception:
                log.exception("지표 핸들러 실행 실패 (market=%s)", market)

        return indicators
