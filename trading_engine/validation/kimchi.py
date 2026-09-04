"""김치 프리미엄 (Step 15) — 국내 시장이 해외보다 얼마나 비싼가.

### 두 가지 프리미엄을 모두 낸다

    원화 프리미엄 (교과서적 김프) = (BTC/KRW ÷ 실환율) ÷ BTC_USD − 1
    코인 프리미엄                  = (BTC/KRW ÷ USDT/KRW) ÷ BTC_USD − 1

**둘은 전혀 다른 값이다.** 처음에 환율 대신 `USDT/KRW` 를 쓰면 되겠다고 생각했는데,
재 보니 범위가 **±0.19%** 밖에 안 나왔다. 이유는 분명하다 — **`USDT/KRW` 가격 자체가
이미 김프를 담고 있어서**, BTC/KRW 를 그것으로 나누면 김프가 상쇄된다. 남는 것은
업비트 안에서 BTC 와 USDT 사이의 차익거래 잔차뿐이라 거의 0 이다.

그래서 실환율이 필요하다. `frankfurter.app`(ECB 기준, API 키 불필요)을 쓴다.

**환율의 한계를 알고 쓴다.** ECB 기준환율은 **평일 하루 한 번**이라 주말·공휴일에는
값이 없다. 암호화폐는 24시간 도는데 환율은 멈춘다. 그래서 직전 영업일 값을 이어
쓰고(`forward fill`), **주말 구간의 프리미엄은 환율이 고정된 상태의 값**이라는 것을
해석할 때 감안해야 한다. 코인 프리미엄은 24시간 돌므로 그 한계가 없다.

### 가설

- **프리미엄이 높다** = 국내 수요 과열 = 고점 근처일 수 있다 (역방향)
- **프리미엄이 낮거나 음수** = 국내 투매 = 저점일 수 있다 (순방향)

둘 다 그럴듯하다. 그래서 **재기 전에는 어느 쪽도 배점에 넣지 않는다.**
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from trading_engine.market.exchange_registry import get_spec
from trading_engine.backtest import data as data_mod

log = logging.getLogger(__name__)

UPBIT = "upbit"
TETHER_KRW = "USDT/KRW"


async def tether_krw(
    since_ms: int, until_ms: int, timeframe: str = "5m"
) -> list[dict[str, Any]]:
    """업비트 USDT/KRW 캔들. 환율 자리에 들어간다.

    `get_spec("upbit").symbol("USDT")` 가 `USDT/KRW` 로 풀린다 — 다른 심볼과 같은
    경로를 쓰므로 별도 클라이언트 관리가 필요 없다.
    """
    return await data_mod.fetch_candles(get_spec(UPBIT), "USDT", timeframe, since_ms, until_ms)


async def upbit_krw(
    base: str, since_ms: int, until_ms: int, timeframe: str = "5m"
) -> list[dict[str, Any]]:
    """업비트 원화 마켓 캔들 (BTC/KRW 등)."""
    return await data_mod.fetch_candles(get_spec(UPBIT), base, timeframe, since_ms, until_ms)


def premium_series(
    upbit_candles: Sequence[Mapping[str, Any]],
    tether_candles: Sequence[Mapping[str, Any]],
    global_series: Sequence[Mapping[str, Any]],
) -> dict[int, float]:
    """봉 인덱스(글로벌 시계열 기준) → 프리미엄 %.

    세 계열의 타임스탬프가 다를 수 있으므로 **글로벌 시계열을 기준으로 맞춘다** —
    신호가 그 위에서 계산되기 때문이다. 짝이 없는 봉은 만들지 않는다.
    """
    krw = {int(c["ts"]): float(c["close"]) for c in upbit_candles}
    usdt = {int(c["ts"]): float(c["close"]) for c in tether_candles}

    out: dict[int, float] = {}
    for index, bar in enumerate(global_series):
        ts = int(bar["ts"])
        krw_price, usdt_price, usd_price = krw.get(ts), usdt.get(ts), float(bar["close"])
        if not krw_price or not usdt_price or usd_price <= 0:
            continue
        out[index] = (krw_price / usdt_price / usd_price - 1.0) * 100.0

    return out


# --- 실환율 (교과서적 김프용) --------------------------------------------------

FX_URL = "https://api.frankfurter.app/{start}..{end}?from=USD&to=KRW"

# ECB 기준환율은 평일 하루 한 번이다. 주말·공휴일에는 직전 영업일 값을 이어 쓴다.
# 이 값보다 오래 이어 써야 하면 환율 소스가 끊긴 것이므로 채우지 않는다.
MAX_FORWARD_FILL_DAYS = 5


async def usd_krw(since_ms: int, until_ms: int) -> dict[int, float]:
    """UTC 날짜(자정 ms) → USD/KRW. `frankfurter.app`, API 키 불필요.

    주말·공휴일은 직전 영업일 값으로 채운다 — 암호화폐는 24시간 도는데 환율은
    멈추기 때문이다. 이 보정이 없으면 주말 구간의 프리미엄이 통째로 사라진다.
    """
    import datetime as dt

    import aiohttp

    start = dt.datetime.fromtimestamp(since_ms / 1000, dt.timezone.utc).date()
    end = dt.datetime.fromtimestamp(until_ms / 1000, dt.timezone.utc).date()
    url = FX_URL.format(start=start.isoformat(), end=end.isoformat())

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            payload = await response.json()

    quoted = {
        dt.date.fromisoformat(day): float(values["KRW"])
        for day, values in sorted(payload.get("rates", {}).items())
        if "KRW" in values
    }
    if not quoted:
        log.warning("환율을 받지 못했다 (%s ~ %s)", start, end)
        return {}

    filled: dict[int, float] = {}
    last_rate: float | None = None
    stale_days = 0
    day = start
    while day <= end:
        if day in quoted:
            last_rate, stale_days = quoted[day], 0
        else:
            stale_days += 1
        if last_rate is not None and stale_days <= MAX_FORWARD_FILL_DAYS:
            midnight = int(
                dt.datetime.combine(day, dt.time.min, dt.timezone.utc).timestamp() * 1000
            )
            filled[midnight] = last_rate
        day += dt.timedelta(days=1)

    return filled


def won_premium_series(
    upbit_candles: Sequence[Mapping[str, Any]],
    fx_by_day: Mapping[int, float],
    global_series: Sequence[Mapping[str, Any]],
) -> dict[int, float]:
    """교과서적 김프. 봉 인덱스 → 프리미엄 %.

    환율은 일별이므로 봉의 UTC 날짜로 찾는다.
    """
    krw = {int(c["ts"]): float(c["close"]) for c in upbit_candles}
    day_ms = 86_400_000

    out: dict[int, float] = {}
    for index, bar in enumerate(global_series):
        ts = int(bar["ts"])
        krw_price = krw.get(ts)
        rate = fx_by_day.get(ts - (ts % day_ms))
        usd_price = float(bar["close"])
        if not krw_price or not rate or usd_price <= 0:
            continue
        out[index] = (krw_price / rate / usd_price - 1.0) * 100.0

    return out
