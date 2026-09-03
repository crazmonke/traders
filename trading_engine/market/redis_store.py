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
    consensus:{symbol}:{tf}             -> String(JSON)  # 거래소 간 합의·점수 (Step 2)
    ai:called:{symbol}:{tf}             -> String(TTL)   # AI 중복 호출 차단 (Step 2)
    ai:result:{symbol}:{tf}             -> String(JSON)  # 그 봉의 AI 분석 (재사용용)
    ai:seed:{symbol}                    -> String(TTL)   # seed 모드 호출 간격 (Step 2)
    watch:{symbol}                      -> String(TTL)   # 이 심볼을 보고 있는 유저 (Step 6·9)
    webhook:rate:{token}:{minute}       -> String(TTL)   # 토큰별 수신 제한 (Step 3-b)
    channel:signals                     -> Pub/Sub       # 확정된 신호 (Step 2)
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


def consensus_key(symbol: str, timeframe: str = "5m") -> str:
    return f"consensus:{symbol}:{timeframe}"


def ai_call_key(symbol: str, timeframe: str = "5m") -> str:
    return f"ai:called:{symbol}:{timeframe}"


def ai_result_key(symbol: str, timeframe: str = "5m") -> str:
    return f"ai:result:{symbol}:{timeframe}"


def ai_seed_key(symbol: str) -> str:
    """seed 모드 호출 간격 키. 타임프레임을 넣지 않는다 — 예산은 돈이고, 5분봉과
    15분봉이 각자 따로 쓰면 예산이 두 배가 된다."""
    return f"ai:seed:{symbol}"


def viewer_key(symbol: str) -> str:
    return f"watch:{symbol}"


SIGNAL_CHANNEL = "channel:signals"


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

    async def save_consensus(
        self, symbol: str, payload: dict[str, Any], timeframe: str = "5m"
    ) -> None:
        """거래소 간 합의 결과. 지표와 같은 TTL 을 준다 — 지표가 낡으면 합의도 낡는다."""
        await self._set_json(
            consensus_key(symbol, timeframe), payload, ttl=self._indicator_ttl
        )

    async def claim_ai_call(self, symbol: str, timeframe: str, ttl: int) -> bool:
        """AI 호출 슬롯을 선점한다. 이미 있으면 False — 그 주기에는 부르지 않는다.

        `SET NX EX` 한 번으로 확인과 선점을 같이 한다. 조회 후 기록으로 나누면 두 심볼
        평가가 겹칠 때 같은 봉에서 두 번 호출될 수 있다. (prompt.md v2 [Step 2] 요구사항 3)
        """
        return bool(
            await self._client.set(ai_call_key(symbol, timeframe), "1", ex=ttl, nx=True)
        )

    async def claim_ai_seed(self, symbol: str, interval: int) -> bool:
        """seed 모드 호출 슬롯. `interval` 초에 한 번만 True 다.

        카운터가 아니라 간격으로 만든 이유는 `strategy/ai_budget.py` 주석에 있다.
        """
        return bool(
            await self._client.set(ai_seed_key(symbol), "1", ex=interval, nx=True)
        )

    async def has_viewer(self, symbol: str) -> bool:
        """이 심볼을 지금 보고 있는 유저가 있는지."""
        return bool(await self._client.exists(viewer_key(symbol)))

    async def mark_viewer(self, symbol: str, ttl: int) -> None:
        """"이 심볼을 보고 있다"를 표시한다. Step 6 API / Step 9 대시보드가 호출한다.

        엔진은 이 값을 읽기만 한다. 조회가 들어오는 쪽에서 갱신해야 하므로,
        키 이름을 각자 만들지 않도록 여기에 둔다.
        """
        await self._client.set(viewer_key(symbol), "1", ex=ttl)

    async def save_ai_analysis(
        self, symbol: str, timeframe: str, payload: dict[str, Any], ttl: int
    ) -> None:
        """그 봉의 AI 분석. 차단 키와 같은 TTL 이라 둘이 함께 만료된다."""
        await self._set_json(ai_result_key(symbol, timeframe), payload, ttl=ttl)

    async def load_ai_analysis(
        self, symbol: str, timeframe: str
    ) -> dict[str, Any] | None:
        raw = await self._client.get(ai_result_key(symbol, timeframe))
        return json.loads(raw) if raw else None

    async def incr_with_expire(self, key: str, ttl: int) -> int:
        """키를 1 올리고 TTL 을 건다. 올린 뒤의 값을 돌려준다.

        INCR 과 EXPIRE 를 파이프라인으로 묶는다. 따로 보내면 두 명령 사이에 프로세스가
        죽었을 때 TTL 없는 키가 남아 그 토큰이 영구히 막힌다.
        """
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl)
        result = await pipe.execute()

        return int(result[0])

    async def publish_signal(self, payload: dict[str, Any]) -> int:
        """확정된 신호를 `channel:signals` 로 publish. 반환값은 수신한 구독자 수."""
        return int(
            await self._client.publish(
                SIGNAL_CHANNEL, json.dumps(payload, ensure_ascii=False)
            )
        )

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
