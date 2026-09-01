"""Upbit 실시간 데이터를 Redis에 캐싱한다.

키 구조는 prompt.md 3.1절을 따른다.
    market:{code}:ticker     -> String(JSON)
    market:{code}:orderbook  -> String(JSON)
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from trading_engine.config import settings


def ticker_key(market: str) -> str:
    return f"market:{market}:ticker"


def orderbook_key(market: str) -> str:
    return f"market:{market}:orderbook"


class RedisStore:
    def __init__(self, client: redis.Redis | None = None, ttl: int | None = None) -> None:
        self._client = client or redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
        )
        self._ttl = settings.cache_ttl_sec if ttl is None else ttl

    @property
    def client(self) -> redis.Redis:
        return self._client

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def save_ticker(self, market: str, payload: dict[str, Any]) -> None:
        await self._set_json(ticker_key(market), payload)

    async def save_orderbook(self, market: str, payload: dict[str, Any]) -> None:
        await self._set_json(orderbook_key(market), payload)

    async def _set_json(self, key: str, payload: dict[str, Any]) -> None:
        await self._client.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl)

    async def close(self) -> None:
        await self._client.aclose()
