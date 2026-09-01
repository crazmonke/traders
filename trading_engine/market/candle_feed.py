"""5분봉 캔들 피드.

기동 시 Upbit REST로 최근 100봉을 시딩하고, 이후 WebSocket 체결 틱으로 현재 봉을 갱신한다.
지표(RSI/MACD/MA)는 캔들이 있어야 계산되므로 수집 시작 직후부터 값이 나오도록 REST 시딩을 둔다.
(prompt.md [Step 1] 요구사항 4 / 3.1절 `market:{code}:candles:5m`)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque

import aiohttp

log = logging.getLogger(__name__)

UPBIT_CANDLE_URL = "https://api.upbit.com/v1/candles/minutes/{minutes}"
CANDLE_INTERVAL_MIN = 5
MAX_CANDLES = 100

# Upbit 시세 REST는 초당 10회 제한. 마켓별 시딩 사이에 여유를 둔다.
SEED_INTERVAL_SEC = 0.15
SEED_TIMEOUT_SEC = 10.0


def interval_ms(minutes: int = CANDLE_INTERVAL_MIN) -> int:
    return minutes * 60 * 1000


def bucket_start(timestamp_ms: int, minutes: int = CANDLE_INTERVAL_MIN) -> int:
    """체결 시각(ms)이 속한 캔들의 시작 시각(ms)."""
    span = interval_ms(minutes)
    return (int(timestamp_ms) // span) * span


def parse_rest_candle(raw: dict[str, Any]) -> dict[str, Any]:
    """Upbit REST 캔들 1건을 내부 표현으로 바꾼다.

    `timestamp` 필드는 봉의 마지막 체결 시각이라 봉 경계와 어긋난다.
    틱 집계와 같은 기준을 쓰려고 `candle_date_time_utc` 를 봉 시작 시각으로 삼는다.
    """
    started = datetime.fromisoformat(raw["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
    return {
        "ts": int(started.timestamp() * 1000),
        "open": float(raw["opening_price"]),
        "high": float(raw["high_price"]),
        "low": float(raw["low_price"]),
        "close": float(raw["trade_price"]),
        "volume": float(raw["candle_acc_trade_volume"]),
    }


class CandleFeed:
    """마켓별 최근 N개 5분봉을 들고 있으면서 체결 틱으로 현재 봉을 갱신한다."""

    def __init__(
        self,
        markets: list[str],
        interval_min: int = CANDLE_INTERVAL_MIN,
        maxlen: int = MAX_CANDLES,
    ) -> None:
        self._markets = list(markets)
        self._interval_min = interval_min
        self._maxlen = maxlen
        self._candles: dict[str, Deque[dict[str, Any]]] = {}

    @property
    def markets(self) -> list[str]:
        return list(self._markets)

    @property
    def interval(self) -> str:
        """Redis 키에 쓰는 인터벌 표기 (예: "5m")."""
        return f"{self._interval_min}m"

    def candles(self, market: str) -> list[dict[str, Any]]:
        """오래된 봉부터 정렬된 사본."""
        return list(self._candles.get(market, ()))

    def seed(self, market: str, candles: list[dict[str, Any]]) -> None:
        """오래된 봉부터 정렬된 리스트로 초기 적재한다."""
        buf: Deque[dict[str, Any]] = deque(maxlen=self._maxlen)
        buf.extend(candles[-self._maxlen :])
        self._candles[market] = buf

    def update_from_tick(self, market: str, tick: dict[str, Any]) -> tuple[dict | None, bool]:
        """체결 틱을 현재 봉에 반영한다. 반환값은 (갱신된 봉, 새 봉 여부)."""
        price = tick.get("trade_price")
        timestamp = tick.get("timestamp")
        if price is None or timestamp is None:
            return None, False

        price = float(price)
        volume = float(tick.get("trade_volume") or 0.0)
        start = bucket_start(timestamp, self._interval_min)
        buf = self._candles.get(market)
        if buf is None:
            buf = self._candles[market] = deque(maxlen=self._maxlen)

        if buf and buf[-1]["ts"] == start:
            current = buf[-1]
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += volume
            return current, False

        if buf and start < buf[-1]["ts"]:
            # 늦게 도착한 틱으로 이미 닫힌 봉을 되돌리지는 않는다.
            return buf[-1], False

        current = {
            "ts": start,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        }
        buf.append(current)
        return current, True

    async def fetch_candles(
        self, session: aiohttp.ClientSession, market: str
    ) -> list[dict[str, Any]]:
        """REST로 최근 봉을 받아 오래된 순으로 돌려준다."""
        url = UPBIT_CANDLE_URL.format(minutes=self._interval_min)
        params = {"market": market, "count": str(self._maxlen)}
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            rows = await resp.json()
        # Upbit는 최신 봉부터 내려준다.
        return [parse_rest_candle(row) for row in reversed(rows)]

    async def seed_all(self, session: aiohttp.ClientSession | None = None) -> int:
        """구독 마켓 전체를 시딩한다. 실패한 마켓은 건너뛰고 성공 개수를 돌려준다.

        시딩에 실패해도 수집은 계속한다. 틱이 쌓이면서 봉이 채워지면 지표도 뒤늦게 나온다.
        """
        owns_session = session is None
        timeout = aiohttp.ClientTimeout(total=SEED_TIMEOUT_SEC)
        session = session or aiohttp.ClientSession(timeout=timeout)
        seeded = 0
        try:
            for index, market in enumerate(self._markets):
                if index:
                    await asyncio.sleep(SEED_INTERVAL_SEC)
                try:
                    candles = await self.fetch_candles(session, market)
                except Exception:
                    log.exception("캔들 시딩 실패 (market=%s) - 틱으로 채운다", market)
                    continue
                self.seed(market, candles)
                seeded += 1
                log.info("캔들 시딩 완료 (market=%s, count=%d)", market, len(candles))
        finally:
            if owns_session:
                await session.close()
        return seeded
