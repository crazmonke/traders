"""심볼별 거래소 간 통합 — 거래량 가중 평균가/지표.

(prompt.md v2 [Step 1] 요구사항 6, Redis `global:{symbol}:*`)

**견적통화가 다른 거래소는 평균에 섞지 않는다.**
업비트는 BTC/KRW(1억 원대), 나머지는 BTC/USDT·USD(7만 달러대)라 그대로 평균 내면
숫자가 무의미해진다. 그래서 글로벌 평균은 USD 계열(USDT/USD) 거래소만으로 내고,
업비트 가격은 `upbit_price` 로 따로 싣는다. DB `ai_signals` 의
`entry_price_global` / `entry_price_upbit` 이 나뉘어 있는 것도 같은 이유다.

USDT 와 USD 는 1:1 로 본다. 스테이블코인 디페그 시 오차가 생기지만 Step 1 범위 밖이다.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from trading_engine.indicators.calculator import Indicators, classify_ma_trend
from trading_engine.market.exchange_registry import get_spec
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# 글로벌 평균에 넣을 견적통화
USD_QUOTES = frozenset({"USDT", "USD", "USDC"})

# 가중 평균을 낼 수치형 지표
WEIGHTED_FIELDS = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "stochastic_k",
    "stochastic_d",
    "adx",
    "cci",
    "orderbook_imbalance",
    "volume_change_rate",
)


def base_of(symbol: str) -> str:
    """'BTC/USDT' → 'BTC'."""
    return symbol.split("/", 1)[0]


def weighted_average(pairs: list[tuple[float, float]]) -> float | None:
    """[(값, 가중치)] 의 가중 평균. 가중치 합이 0이면 단순 평균으로 물러선다."""
    usable = [(value, weight) for value, weight in pairs if value is not None]
    if not usable:
        return None
    total_weight = sum(weight for _, weight in usable if weight > 0)
    if total_weight <= 0:
        return sum(value for value, _ in usable) / len(usable)
    return sum(value * weight for value, weight in usable if weight > 0) / total_weight


class MarketManager:
    """거래소별 지표·시세를 모아 심볼 단위로 합친다."""

    def __init__(self, store: RedisStore) -> None:
        self._store = store
        self._indicators: dict[tuple[str, str], Indicators] = {}
        self._tickers: dict[tuple[str, str], dict[str, Any]] = {}

    def record_ticker(self, exchange: str, symbol: str, payload: dict[str, Any]) -> None:
        self._tickers[(exchange, symbol)] = payload

    def record_indicators(self, exchange: str, symbol: str, indicators: Indicators) -> None:
        self._indicators[(exchange, symbol)] = indicators

    def _weight(self, exchange: str, symbol: str) -> float:
        """가중치는 24시간 견적통화 거래대금. 없으면 0(동등 가중으로 물러선다)."""
        ticker = self._tickers.get((exchange, symbol)) or {}
        try:
            return float(ticker.get("quote_volume_24h") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def aggregate(self, base: str) -> dict[str, Any] | None:
        """한 심볼의 글로벌 스냅샷. 유효한 거래소가 하나도 없으면 None."""
        prices: list[tuple[float, float]] = []
        field_values: dict[str, list[tuple[float, float]]] = {f: [] for f in WEIGHTED_FIELDS}
        mas: dict[str, list[tuple[float, float]]] = {"ma5": [], "ma20": [], "ma60": []}
        sources: list[str] = []
        upbit_price: float | None = None

        for (exchange, symbol), indicators in self._indicators.items():
            if base_of(symbol) != base:
                continue
            try:
                quote = get_spec(exchange).quote
            except KeyError:
                continue

            if quote not in USD_QUOTES:
                # 견적통화가 다르면 평균에서 빼되 값 자체는 버리지 않는다.
                if exchange == "upbit" and indicators.close is not None:
                    upbit_price = indicators.close
                continue

            weight = self._weight(exchange, symbol)
            sources.append(exchange)
            if indicators.close is not None:
                prices.append((indicators.close, weight))
            for field in WEIGHTED_FIELDS:
                value = getattr(indicators, field, None)
                if value is not None:
                    field_values[field].append((float(value), weight))
            for name in mas:
                value = getattr(indicators, name, None)
                if value is not None:
                    mas[name].append((float(value), weight))

        if not sources:
            return None

        merged_ma = {name: weighted_average(values) for name, values in mas.items()}
        return {
            "symbol": base,
            "price": weighted_average(prices),
            "upbit_price": upbit_price,
            "sources": sorted(sources),
            "source_count": len(sources),
            **{field: weighted_average(values) for field, values in field_values.items()},
            **merged_ma,
            "ma_trend": classify_ma_trend(
                merged_ma["ma5"], merged_ma["ma20"], merged_ma["ma60"]
            ),
            "updated_at": int(time.time() * 1000),
        }

    async def publish(self, base: str) -> dict[str, Any] | None:
        """글로벌 스냅샷을 계산해 Redis 에 쓴다."""
        snapshot = self.aggregate(base)
        if snapshot is None:
            return None
        try:
            await self._store.save_global_price(
                base,
                {
                    "symbol": base,
                    "price": snapshot["price"],
                    "upbit_price": snapshot["upbit_price"],
                    "sources": snapshot["sources"],
                    "source_count": snapshot["source_count"],
                    "updated_at": snapshot["updated_at"],
                },
            )
            await self._store.save_global_indicators(base, snapshot)
        except Exception:
            log.exception("글로벌 집계 캐싱 실패 (symbol=%s)", base)
        return snapshot
