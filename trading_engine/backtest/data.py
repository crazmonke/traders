"""백테스트용 과거 캔들 수집. (prompt.md v2 [Step 4] 요구사항 1·2)

거래소 REST(`ccxt`)로 캔들을 긁어 오고, 신호 검증용(`GLOBAL_CONSENSUS`)에 쓸
**합성 글로벌 OHLC** 를 만든다.

글로벌 OHLC 는 운영의 글로벌 가격과 같은 규칙으로 만든다 — 견적통화가 USD 계열인
거래소만, 거래대금 가중 평균. 업비트(KRW)를 섞으면 BTC 1억과 7만이 한 평균에 들어간다
(`market_manager` 모듈 주석과 같은 이유).
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from trading_engine.market.exchange_registry import ExchangeSpec, create_client, get_spec
from trading_engine.market.market_manager import USD_QUOTES, weighted_average

log = logging.getLogger(__name__)

# 한 번에 요청할 봉 수. **거래소가 이만큼 준다는 보장이 없다** — 실측으로
# binance/bybit 576, okx 300, upbit 200 처럼 제각각이었다(2026-09-03).
# 그래서 "받은 개수가 요청보다 적으면 끝"이라고 판단하면 안 된다. 커서가 더 못 나갈 때만 멈춘다.
FETCH_LIMIT = 1000

# 한 거래소·한 구간에 허용할 최대 페이지 수. 거래소가 한 번에 한두 봉씩만 주는
# 경우에도 루프가 끝나도록 하는 안전장치다.
MAX_PAGES = 200


def to_candles(rows: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    """ccxt OHLCV 배열 → 우리 캔들 딕셔너리."""
    return [
        {
            "ts": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


async def fetch_candles(
    spec: ExchangeSpec,
    base: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
) -> list[dict[str, Any]]:
    """한 거래소의 구간 캔들. 페이지를 넘겨 가며 모은다.

    거래소가 `since` 를 무시하거나 같은 페이지를 반복해서 주는 경우가 있어, 진행이
    멈추면 루프를 끊는다. 무한 루프로 API 를 두드리는 것이 더 나쁘다.
    """
    client = create_client(spec)
    symbol = spec.symbol(base)
    collected: list[dict[str, Any]] = []
    cursor = since_ms

    try:
        for _ in range(MAX_PAGES):
            if cursor >= until_ms:
                break
            rows = await client.fetch_ohlcv(symbol, timeframe, since=cursor, limit=FETCH_LIMIT)
            if not rows:
                break
            page = [candle for candle in to_candles(rows) if candle["ts"] <= until_ms]
            if not page:
                break
            collected.extend(page)

            last_ts = page[-1]["ts"]
            if last_ts <= cursor:  # 진행이 없다 - 거래소가 같은 구간을 반복해 준다
                break
            cursor = last_ts + 1
    finally:
        await client.close()

    return dedupe(collected)


def dedupe(candles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 ts 중복 제거 + 시간순 정렬. 페이지 경계에서 겹쳐 오는 봉을 정리한다."""
    unique: dict[int, dict[str, Any]] = {}
    for candle in candles:
        unique[int(candle["ts"])] = candle

    return [unique[ts] for ts in sorted(unique)]


TIMEFRAME_UNITS = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 7 * 86_400_000,
    # 월은 28~31일로 길이가 고정이 아니다. 여기 값은 **근사치**이고, 구멍 판정에서만
    # 쓰이므로 아래 GAP_TOLERANCE 가 그 편차를 흡수한다. 정확한 달력 계산이 필요한
    # 곳에는 쓰지 말 것.
    "M": 30 * 86_400_000,
}

# 이 배수를 넘게 벌어지면 "구멍"으로 본다. 정확히 한 칸을 요구하면 월봉(28~31일)이
# 매번 불연속으로 잡히고, 거래소의 미세한 타임스탬프 차이에도 걸린다.
GAP_TOLERANCE = 1.5


def timeframe_ms(timeframe: str) -> int:
    """'5m' → 300000. 봉 간격을 알아야 데이터 구멍을 판정할 수 있다.

    대소문자를 구분한다 — 'm' 은 분, 'M' 은 월이다(ccxt 관례).
    """
    value, unit = timeframe[:-1], timeframe[-1]
    try:
        return int(value) * TIMEFRAME_UNITS[unit]
    except (ValueError, KeyError) as error:
        raise ValueError(f"해석할 수 없는 타임프레임이다: {timeframe!r}") from error


def trim_to_contiguous(
    per_exchange: dict[str, list[dict[str, Any]]], bucket_ms: int
) -> dict[str, list[dict[str, Any]]]:
    """가장 긴 **연속** 구간만 남긴다. `align` 뒤에 부른다.

    거래소가 구간 중간을 통째로 빼먹고 주는 일이 있다 — 실측으로 업비트 7일 요청에
    **801봉(약 67시간)짜리 구멍**이 하나 있었다(2026-09-03). 구멍을 그대로 두면 100봉
    지표 창이 그 구멍을 가로질러, 3일 떨어진 봉들을 연속인 것처럼 계산한다.
    RSI·MACD·볼린저가 전부 거짓이 되고 백테스트 결과도 거짓이 된다.

    비어 있는 구간을 채우지 않는다(없는 시세를 지어내는 것이다). 연속인 구간만 쓰고,
    짧아진 사실은 호출한 쪽이 로그로 남긴다.
    """
    if not per_exchange:
        return {}

    stamps = sorted({int(c["ts"]) for c in next(iter(per_exchange.values()))})
    if not stamps:
        return per_exchange

    best_start = start = 0
    best_len = 1
    limit = bucket_ms * GAP_TOLERANCE
    for index in range(1, len(stamps)):
        if stamps[index] - stamps[index - 1] > limit:
            start = index
        elif index - start + 1 > best_len:
            best_len = index - start + 1
            best_start = start

    keep = set(stamps[best_start : best_start + best_len])
    if len(keep) == len(stamps):
        return per_exchange

    return {
        code: [c for c in candles if int(c["ts"]) in keep]
        for code, candles in per_exchange.items()
    }


def align(
    per_exchange: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """모든 거래소가 공통으로 가진 타임스탬프만 남긴다.

    거래소마다 상장 시점과 결측 봉이 다르다. 맞추지 않으면 같은 인덱스가 서로 다른
    시각을 가리켜 합의 계산이 통째로 어긋난다.
    """
    if not per_exchange:
        return {}

    common: set[int] | None = None
    for candles in per_exchange.values():
        stamps = {int(candle["ts"]) for candle in candles}
        common = stamps if common is None else (common & stamps)

    if not common:
        return {}

    return {
        code: [candle for candle in candles if int(candle["ts"]) in common]
        for code, candles in per_exchange.items()
    }


def global_price_series(
    per_exchange: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """USD 계열 거래소만 거래대금 가중 평균한 합성 OHLC.

    운영의 `global:{symbol}:price` 와 같은 규칙이다. 이 값이 신호 검증용 백테스트의
    체결 기준가가 된다.
    """
    usd = {
        code: candles
        for code, candles in per_exchange.items()
        if _quote_of(code) in USD_QUOTES
    }
    if not usd:
        return []

    length = min(len(candles) for candles in usd.values())
    series: list[dict[str, Any]] = []

    for index in range(length):
        bars = [candles[index] for candles in usd.values()]
        weights = [bar["close"] * bar["volume"] for bar in bars]
        merged = {
            field: weighted_average(
                [(bar[field], weight) for bar, weight in zip(bars, weights)]
            )
            for field in ("open", "high", "low", "close")
        }
        if any(value is None for value in merged.values()):
            continue
        series.append(
            {
                "ts": int(bars[0]["ts"]),
                **merged,
                "volume": sum(bar["volume"] for bar in bars),
            }
        )

    return series


def single_exchange_series(
    per_exchange: dict[str, list[dict[str, Any]]], code: str
) -> list[dict[str, Any]]:
    """특정 거래소 단독 기준가 (요구사항 2). Step 4-b 의 업비트 실전용이 쓴다."""
    return list(per_exchange.get(code, []))


def _quote_of(code: str) -> str:
    try:
        return get_spec(code).quote
    except KeyError:
        return "USDT"  # 레지스트리에 없는 코드(테스트용)는 USD 계열로 본다
