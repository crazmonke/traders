"""Upbit 실시간 데이터를 Redis에 캐싱한다.

키 구조는 prompt.md 3.1절을 따른다.
    market:{code}:ticker      -> String(JSON)
    market:{code}:orderbook   -> String(JSON)
    market:{code}:candles:5m  -> List(JSON 요소, 오래된 봉부터)
    market:{code}:indicators  -> String(JSON)

v2 다중 거래소 키는 prompt.md v2 3.1절을 따른다.
    exchange:{code}:{symbol}:ticker     -> String(JSON)
    exchange:{code}:{symbol}:orderbook  -> String(JSON)
    exchange:{code}:{symbol}:candles:5m -> List(JSON 요소, 오래된 봉부터)
    exchange:{code}:{symbol}:indicators -> String(JSON)
    global:{symbol}:price               -> String(JSON)  # 거래량 가중 평균
    global:{symbol}:indicators          -> String(JSON)
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import redis.asyncio as redis

from trading_engine.config import settings


def ticker_key(market: str) -> str:
    return f"market:{market}:ticker"


def orderbook_key(market: str) -> str:
    return f"market:{market}:orderbook"


def candles_key(market: str, interval: str = "5m") -> str:
    return f"market:{market}:candles:{interval}"


def indicators_key(market: str) -> str:
    return f"market:{market}:indicators"


def exchange_key(exchange: str, symbol: str, kind: str) -> str:
    """거래소별 키. 심볼의 '/' 는 Redis 키에서 보기 나쁘므로 '-' 로 바꾼다."""
    return f"exchange:{exchange}:{symbol.replace('/', '-')}:{kind}"


def global_key(symbol: str, kind: str) -> str:
    return f"global:{symbol}:{kind}"


class RedisStore:
    def __init__(
        self,
        client: redis.Redis | None = None,
        ttl: int | None = None,
        candle_ttl: int | None = None,
        indicator_ttl: int | None = None,
    ) -> None:
        self._client = client or redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
        )
        self._ttl = settings.cache_ttl_sec if ttl is None else ttl
        self._candle_ttl = settings.candle_cache_ttl_sec if candle_ttl is None else candle_ttl
        self._indicator_ttl = (
            settings.indicator_cache_ttl_sec if indicator_ttl is None else indicator_ttl
        )

    @property
    def client(self) -> redis.Redis:
        return self._client

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def save_ticker(self, market: str, payload: dict[str, Any]) -> None:
        await self._set_json(ticker_key(market), payload)

    async def save_orderbook(self, market: str, payload: dict[str, Any]) -> None:
        await self._set_json(orderbook_key(market), payload)

    async def save_exchange_ticker(
        self, exchange: str, symbol: str, payload: dict[str, Any]
    ) -> None:
        await self._set_json(exchange_key(exchange, symbol, "ticker"), payload)

    async def save_exchange_orderbook(
        self, exchange: str, symbol: str, payload: dict[str, Any]
    ) -> None:
        await self._set_json(exchange_key(exchange, symbol, "orderbook"), payload)

    async def save_exchange_indicators(
        self, exchange: str, symbol: str, payload: dict[str, Any]
    ) -> None:
        await self._set_json(
            exchange_key(exchange, symbol, "indicators"), payload, ttl=self._indicator_ttl
        )

    async def save_exchange_candles(
        self,
        exchange: str,
        symbol: str,
        candles: Sequence[dict[str, Any]],
        interval: str = "5m",
    ) -> None:
        await self._replace_list(
            exchange_key(exchange, symbol, f"candles:{interval}"), candles, self._candle_ttl
        )

    async def save_global_price(self, symbol: str, payload: dict[str, Any]) -> None:
        await self._set_json(global_key(symbol, "price"), payload, ttl=self._indicator_ttl)

    async def save_global_indicators(self, symbol: str, payload: dict[str, Any]) -> None:
        await self._set_json(global_key(symbol, "indicators"), payload, ttl=self._indicator_ttl)

    async def save_indicators(self, market: str, payload: dict[str, Any]) -> None:
        await self._set_json(indicators_key(market), payload, ttl=self._indicator_ttl)

    async def save_candles(
        self, market: str, candles: Sequence[dict[str, Any]], interval: str = "5m"
    ) -> None:
        """캔들 List를 통째로 교체한다. 봉이 닫힐 때만 호출하므로 비용은 문제되지 않는다."""
        await self._replace_list(candles_key(market, interval), candles, self._candle_ttl)

    async def _replace_list(
        self, key: str, items: Sequence[dict[str, Any]], ttl: int
    ) -> None:
        pipe = self._client.pipeline()
        pipe.delete(key)
        if items:
            pipe.rpush(key, *[json.dumps(item, ensure_ascii=False) for item in items])
            pipe.expire(key, ttl)
        await pipe.execute()

    async def load_candles(self, market: str, interval: str = "5m") -> list[dict[str, Any]]:
        rows = await self._client.lrange(candles_key(market, interval), 0, -1)
        return [json.loads(row) for row in rows]

    async def _set_json(self, key: str, payload: dict[str, Any], ttl: int | None = None) -> None:
        await self._client.set(
            key, json.dumps(payload, ensure_ascii=False), ex=self._ttl if ttl is None else ttl
        )

    async def close(self) -> None:
        await self._client.aclose()
