"""시그널 성과 자동 추적 (prompt.md v2 [Step 7]).

과거에 낸 신호가 실제로 어떻게 됐는지를 `ai_signal_results` 에 기록한다.
**적중률은 이 서비스가 광고할 수 있는 유일한 실적**이라(법률 검토 `docs/LEGAL.md`),
여기서 재는 방식이 곧 대외적으로 말할 수 있는 내용의 한계가 된다.

### 정답 정의는 하나뿐이다

판정은 전부 `strategy/labeling.py` 가 한다. 백테스트도 같은 모듈을 쓴다.
스펙 원안의 "5분 뒤 ±0.2%" 는 쓰지 않는다 — 왕복 수수료(0.18~0.20%)를 빼면 기댓값이
0 에 수렴해서, 맞춰도 남는 게 없는 정답을 쫓게 된다. 근거는 `labeling.py` 첫머리.

### 왜 거래소에서 캔들을 다시 받아오는가

Redis 에는 최신 시세만 남고 과거 구간은 없다. 그리고 무엇보다 **백테스트와 같은 함수로
같은 글로벌 가중평균 가격 계열을 만들어야** 두 숫자를 나란히 놓고 볼 수 있다
(`backtest.data.global_price_series`). 여기서만 다른 가격을 쓰면 "백테스트는 좋은데
적중률은 나쁘다"가 나와도 원인을 가릴 수 없다.

### 언제 평가하는가

**시간 제한이 다 지난 뒤에만** 평가한다. 아직 안 끝난 신호를 미리 기록하면 배리어에
닿기 전 상태가 `TIME_LIMIT` 으로 굳는다. 그래서 horizon 만큼 + 봉 하나가 더 지나야
(마지막 봉이 닫혀야) 대상이 된다.

한 신호는 horizon 5개(5m·15m·1h·4h·1d)로 **각각** 평가된다. 배리어까지 걸린 시간이
곧 "얼마나 들고 있어야 하는가"이고, 짧은 horizon 이 대부분 `TIME_LIMIT` 이면 그 신호가
단기용이 아니라는 사실이 데이터로 드러나야 한다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from trading_engine.backtest import data as backtest_data
from trading_engine.backtest.runner import collect
from trading_engine.config import settings
from trading_engine.database import mysql
from trading_engine.market.exchange_feed import CANDLE_TIMEFRAME
from trading_engine.market.redis_store import RedisStore
from trading_engine.strategy import labeling

log = logging.getLogger(__name__)

MINUTE_MS = 60_000

# 봉 하나 길이(분). 마지막 봉이 닫혔는지 판단하는 데 쓴다.
CANDLE_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

LOCK_NAME = "signal-result-tracker"

# 한 번에 처리할 (신호 × horizon) 수. 거래소 호출이 들어가므로 무한정 늘리지 않는다.
BATCH_LIMIT = 500

# 이보다 오래된 신호는 포기한다. 캔들을 못 받아오는 신호가 섞이면 배치가 매번 같은
# 건에서 막혀 새 신호가 영영 평가되지 않는다.
MAX_AGE_DAYS = 30

PENDING_SQL = """
SELECT s.id, s.symbol, s.signal_type, s.entry_price_global,
       UNIX_TIMESTAMP(s.created_at) AS created_ts
FROM ai_signals s
LEFT JOIN ai_signal_results r ON r.signal_id = s.id AND r.horizon = %s
WHERE r.id IS NULL
  AND s.signal_type NOT IN ({unlabeled})
  AND s.created_at <= NOW() - INTERVAL %s MINUTE
  AND s.created_at >= NOW() - INTERVAL %s DAY
ORDER BY s.created_at
LIMIT %s
"""

# 같은 (signal_id, horizon) 을 두 번 넣지 않는다. 데몬이 겹쳐 돌아도 결과가 같아야 한다.
UPSERT_SQL = """
INSERT INTO ai_signal_results
    (signal_id, horizon, price_entry, price_after, return_pct,
     exit_reason, best_price, worst_price, is_accurate, evaluated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON DUPLICATE KEY UPDATE
    price_after = VALUES(price_after),
    return_pct = VALUES(return_pct),
    exit_reason = VALUES(exit_reason),
    best_price = VALUES(best_price),
    worst_price = VALUES(worst_price),
    is_accurate = VALUES(is_accurate),
    evaluated_at = VALUES(evaluated_at)
"""


@dataclass(frozen=True)
class PendingSignal:
    """아직 평가되지 않은 (신호, horizon) 한 건."""

    signal_id: int
    symbol: str
    signal_type: str
    entry_price: float
    created_ts_ms: int
    horizon: str


def _candle_minutes(timeframe: str) -> int:
    minutes = CANDLE_MINUTES.get(timeframe)
    if minutes is None:
        raise ValueError(f"봉 길이를 모르는 타임프레임이다: {timeframe!r}")
    return minutes


def ready_after_minutes(horizon: str, timeframe: str = CANDLE_TIMEFRAME) -> int:
    """이 horizon 을 판정하려면 신호 생성 후 몇 분이 지나야 하는가.

    horizon 만큼으로는 모자란다 — 마지막 봉이 아직 닫히지 않았으면 그 봉의 고가·저가가
    확정되지 않아 배리어 판정이 뒤집힐 수 있다. 봉 하나를 더 기다린다.
    """
    return labeling.HORIZONS[horizon] + _candle_minutes(timeframe)


async def pending(
    horizon: str,
    limit: int = BATCH_LIMIT,
    timeframe: str = CANDLE_TIMEFRAME,
) -> list[PendingSignal]:
    """이 horizon 으로 아직 평가되지 않은 신호들. 오래된 것부터."""
    placeholders = ", ".join(["%s"] * len(labeling.UNLABELED_SIGNALS))
    sql = PENDING_SQL.format(unlabeled=placeholders)
    params = (
        horizon,
        *sorted(labeling.UNLABELED_SIGNALS),
        ready_after_minutes(horizon, timeframe),
        MAX_AGE_DAYS,
        limit,
    )
    rows = await mysql.fetch_all(sql, params)

    return [
        PendingSignal(
            signal_id=int(row["id"]),
            symbol=str(row["symbol"]),
            signal_type=str(row["signal_type"]),
            entry_price=float(row["entry_price_global"]),
            created_ts_ms=int(row["created_ts"]) * 1000,
            horizon=horizon,
        )
        for row in rows
        if row["entry_price_global"] is not None
    ]


async def price_series(
    symbol: str, since_ms: int, until_ms: int, timeframe: str = CANDLE_TIMEFRAME
) -> list[dict[str, Any]]:
    """백테스트와 **같은 함수로** 글로벌 거래량 가중 평균 캔들을 만든다."""
    per_exchange = await collect(symbol, since_ms, until_ms, timeframe=timeframe)
    if not per_exchange:
        return []
    return list(backtest_data.global_price_series(per_exchange))


def forward_bars(
    series: Sequence[Mapping[str, Any]], created_ts_ms: int
) -> list[Mapping[str, Any]]:
    """신호 **이후에 열린** 봉만. 신호 시각이 걸쳐 있는 봉은 뺀다.

    그 봉의 고가·저가에는 신호가 나기 전의 움직임이 섞여 있다. 그대로 쓰면 신호를 내기
    전에 이미 닿았던 가격으로 배리어가 판정될 수 있다.
    """
    return [bar for bar in series if int(bar["ts"]) > created_ts_ms]


def evaluate(
    item: PendingSignal,
    series: Sequence[Mapping[str, Any]],
    timeframe: str = CANDLE_TIMEFRAME,
) -> labeling.Label | None:
    """한 건을 판정한다. 봉이 모자라거나 구멍이 있으면 None — 다음 주기에 다시 시도한다."""
    bars = forward_bars(series, item.created_ts_ms)
    if not bars:
        return None

    candle_ms = _candle_minutes(timeframe) * MINUTE_MS

    # 신호 직후 봉이 없으면 판정하지 않는다. `labeling` 은 시간 제한을 `bars[0]` 기준으로
    # 재므로, 첫 봉이 신호보다 한참 뒤면 **엉뚱한 구간을 그 horizon 으로 재게 된다**
    # (예: 신호 3시간 뒤부터 시작하는 봉으로 "1시간 후" 를 판정).
    # 거래소 장애나 수집 구멍에서 실제로 생길 수 있다.
    if int(bars[0]["ts"]) - item.created_ts_ms > candle_ms:
        return None

    # 시간 제한을 다 덮을 만큼 봉이 있는지. 모자란 채로 판정하면 아직 닿을 수 있는
    # 배리어를 놓치고 TIME_LIMIT 으로 굳어버린다.
    span_ms = int(bars[-1]["ts"]) - int(bars[0]["ts"])
    if span_ms < (labeling.HORIZONS[item.horizon] - 1) * MINUTE_MS:
        return None

    return labeling.label(item.entry_price, item.signal_type, bars, item.horizon)


async def save(item: PendingSignal, label: labeling.Label) -> None:
    await mysql.execute(
        UPSERT_SQL,
        (
            item.signal_id,
            label.horizon,
            round(item.entry_price, 8),
            round(label.exit_price, 8),
            round(label.return_pct, 2),
            label.exit_reason,
            round(label.best_price, 8),
            round(label.worst_price, 8),
            1 if label.is_accurate else 0,
        ),
    )


def _window(items: Iterable[PendingSignal]) -> tuple[int, int]:
    """이 심볼의 모든 대기 건을 덮는 캔들 구간 (시작, 끝).

    심볼마다 한 번만 받아오려고 구간을 합친다 — 건별로 받으면 같은 캔들을 수십 번
    다시 받는다.
    """
    items = list(items)
    oldest = min(item.created_ts_ms for item in items)
    newest = max(
        item.created_ts_ms + labeling.HORIZONS[item.horizon] * MINUTE_MS for item in items
    )
    # 시작 쪽에 봉 하나를 여유로 둔다(경계 봉이 잘리지 않게).
    return oldest - 5 * MINUTE_MS, min(newest + 5 * MINUTE_MS, int(time.time() * 1000))


async def run_once(timeframe: str = CANDLE_TIMEFRAME) -> int:
    """대기 중인 모든 (신호 × horizon) 을 한 번 훑는다. 기록한 건수를 돌려준다."""
    batch: list[PendingSignal] = []
    for horizon in labeling.HORIZONS:
        batch.extend(await pending(horizon, timeframe=timeframe))

    if not batch:
        return 0

    by_symbol: dict[str, list[PendingSignal]] = {}
    for item in batch:
        by_symbol.setdefault(item.symbol, []).append(item)

    log.info(
        "평가 대기 %d건 (심볼 %d개): %s",
        len(batch),
        len(by_symbol),
        ", ".join(f"{sym} {len(items)}" for sym, items in sorted(by_symbol.items())),
    )

    saved = 0
    for symbol, items in by_symbol.items():
        since, until = _window(items)
        try:
            series = await price_series(symbol, since, until, timeframe)
        except Exception:
            # 한 심볼의 수집 실패가 나머지 심볼 평가를 막지 않게 한다.
            log.exception("캔들 수집 실패 - 이 심볼은 다음 주기에 다시 시도한다 (%s)", symbol)
            continue

        if not series:
            log.warning("캔들이 비어 평가를 미룬다 (%s)", symbol)
            continue

        pending_again = 0
        for item in items:
            label = evaluate(item, series, timeframe)
            if label is None:
                pending_again += 1
                continue
            try:
                await save(item, label)
                saved += 1
            except Exception:
                log.exception(
                    "결과 저장 실패 (signal_id=%s horizon=%s)", item.signal_id, item.horizon
                )

        if pending_again:
            log.info("%s: %d건은 봉이 모자라 다음 주기로 미뤘다", symbol, pending_again)

    log.info("평가 완료 %d건 기록", saved)
    return saved


async def run_once_locked(store: RedisStore, ttl: int | None = None) -> int:
    """분산 락을 잡고 한 주기를 돈다. 이미 누가 돌고 있으면 건너뛴다.

    락은 "두 번 돌지 않게" 하는 것이지 정합성을 지키는 장치가 아니다 — 정합성은
    `INSERT ... ON DUPLICATE KEY UPDATE` 가 지킨다. 그래서 락을 못 잡으면 그냥 넘어간다.
    """
    ttl = ttl or settings.tracker_lock_ttl_sec
    token = await store.claim_lock(LOCK_NAME, ttl)
    if token is None:
        log.info("다른 인스턴스가 평가 중이다 - 이번 주기는 건너뛴다")
        return 0

    try:
        return await run_once()
    finally:
        await store.release_lock(LOCK_NAME, token)


async def run_forever(store: RedisStore, interval: int | None = None) -> None:
    """스케줄러 본체. 주기마다 한 번씩 평가한다.

    한 주기가 실패해도 데몬을 죽이지 않는다 — 거래소 장애로 몇 분 밀리는 것은 정상이고,
    미평가 건은 다음 주기에 그대로 다시 잡힌다.
    """
    interval = interval or settings.tracker_interval_sec
    log.info("성과 추적 시작 (%d초 주기, horizon %s)", interval, ", ".join(labeling.HORIZONS))

    while True:
        started = time.monotonic()
        try:
            await run_once_locked(store)
        except Exception:
            log.exception("평가 주기 실패 - 다음 주기에 다시 시도한다")

        elapsed = time.monotonic() - started
        await asyncio.sleep(max(interval - elapsed, 1.0))


async def _main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    store = RedisStore()
    try:
        await store.ping()
        await run_forever(store)
    finally:
        await store.close()


def main() -> None:
    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())


if __name__ == "__main__":
    main()
