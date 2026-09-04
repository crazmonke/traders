"""워크포워드 검증 (Step 17) — "이 변경이 시기를 넘어 유효한가"를 자동으로 묻는다.

### 왜 만들었나

같은 함정에 두 번 빠졌다.

- **Step 16**: 최근 14일에서 RSI 과매도가 가장 나빴는데 두 달 전에는 가장 좋았다.
- **Step 14**: "신규 롱 + 펀딩 양수"가 두 구간에서 +0.36% / +1.15% 였는데 나머지
  한 구간에서 -0.234% 였다. 두 구간만 봤다면 채택했을 것이다.

두 번 다 **즉석 스크립트로 3구간을 돌려** 겨우 피했다. 3구간으로는 부호가 우연히
맞을 확률이 25% 라 사실 피한 것도 운이었다. 이 파일은 그 질문을 **12구간에서
자동으로, 캐시를 재사용하며** 묻는다. 검증이 싸져야 실제로 검증한다.

### 쓰는 법

    # 기준선만: 지금 전략이 구간마다 어떤 성적인지
    python -m trading_engine.validation.walkforward

    # 변형 비교: 익절·손절 폭을 바꾸면 나아지는가
    python -m trading_engine.validation.walkforward \\
        --variant take_profit_pct=3.0,stop_loss_pct=1.5

    # 진입 임계값을 올리면 나아지는가
    python -m trading_engine.validation.walkforward --variant min_final_score=85

판정 규칙은 `stability.py` 에 있고, 눈대중이 아니라 부호 검정이다.

### 무엇을 재는가

기본 지표는 **거래당 무비용 손익 − 왕복 비용**이다. 총수익률이 아니라 거래당으로 재는
이유는, 총수익률이 "거래를 몇 번 했는가"에 지배되기 때문이다. Step 16 에서 배리어를
넓힐수록 총수익이 좋아졌는데 그 원인이 신호 개선이 아니라 **거래 수 감소**였다.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import statistics
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from trading_engine.backtest import costs as costs_mod
from trading_engine.backtest import data as data_mod
from trading_engine.backtest.costs import GLOBAL_CONSENSUS, CostModel
from trading_engine.backtest.engine import WINDOW, BacktestParams, Backtester
from trading_engine.validation import cache, stability
from trading_engine.validation.windows import Window, generate

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ("BTC", "ETH", "SOL")
NO_COST = CostModel(fee_rate=0.0, slippage_rate=0.0, label="none")


@dataclass(frozen=True)
class Cell:
    """한 (심볼 × 구간) 결과."""

    symbol: str
    window: Window
    trades: int
    net_pct: float
    gross_pct: float

    @property
    def per_trade_gross(self) -> float:
        """거래당 무비용 손익. **이것이 비용을 넘어야 실거래에서 남는다.**"""
        return self.gross_pct / self.trades if self.trades else 0.0

    @property
    def edge(self) -> float:
        """거래당 손익에서 왕복 비용을 뺀 값."""
        return self.per_trade_gross - stability.ROUND_TRIP_COST_PCT


def evaluate(
    params: BacktestParams,
    exchange_candles: Mapping[str, Sequence[dict[str, Any]]],
    price_series: Sequence[dict[str, Any]],
    window: Window,
) -> Cell | None:
    """한 구간을 돌린다. 신호는 한 번만 계산하고 비용 모델만 바꿔 두 번 시뮬레이션한다."""
    if len(price_series) <= WINDOW + 2:
        return None

    tester = Backtester(params, costs_mod.REFERENCE)
    signals = tester.compute_signals(exchange_candles, price_series)
    if not signals:
        return None

    net = tester.run(exchange_candles, price_series, signals).metrics
    gross = Backtester(params, NO_COST).run(exchange_candles, price_series, signals).metrics

    return Cell(
        symbol=params.symbol,
        window=window,
        trades=net.total_trades,
        net_pct=net.total_return_pct,
        gross_pct=gross.total_return_pct,
    )


async def run(
    symbols: Sequence[str],
    windows: Sequence[Window],
    overrides: Mapping[str, Any] | None = None,
    timeframe: str = "5m",
) -> list[Cell]:
    """모든 (심볼 × 구간) 을 돈다. 캔들은 캐시에서 온다."""
    cells: list[Cell] = []
    for window in windows:
        for symbol in symbols:
            candles = await cache.candles(symbol, window.since_ms, window.until_ms, timeframe)
            if not candles:
                log.warning("%s %s: 캔들 없음", symbol, window.label)
                continue
            series = data_mod.global_price_series(candles)
            params = BacktestParams(
                symbol=symbol, reference_exchange=GLOBAL_CONSENSUS, timeframe=timeframe
            )
            if overrides:
                params = dataclasses.replace(params, **overrides)
            cell = evaluate(params, candles, series, window)
            if cell is not None:
                cells.append(cell)
        print(f"  {window.label} 완료", flush=True)
    return cells


def by_window(cells: Sequence[Cell]) -> dict[int, list[Cell]]:
    grouped: dict[int, list[Cell]] = {}
    for cell in cells:
        grouped.setdefault(cell.window.index, []).append(cell)
    return grouped


def window_edges(cells: Sequence[Cell]) -> dict[int, float]:
    """구간별 거래당 초과손익(심볼 평균). 부호 검정의 표본이 된다."""
    return {
        index: statistics.mean([c.edge for c in group])
        for index, group in sorted(by_window(cells).items())
    }


def parse_overrides(text: str) -> dict[str, Any]:
    """`take_profit_pct=3.0,min_final_score=85` → dict. 값은 float 로 읽는다."""
    fields = {f.name: f.type for f in dataclasses.fields(BacktestParams)}
    out: dict[str, Any] = {}
    for chunk in text.split(","):
        if not chunk.strip():
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if key not in fields:
            raise SystemExit(
                f"BacktestParams 에 없는 항목이다: {key!r}\n"
                f"가능한 항목: {', '.join(sorted(fields))}"
            )
        out[key] = float(value) if value.replace(".", "", 1).lstrip("-").isdigit() else value
    return out


def report(title: str, cells: Sequence[Cell], now_ms: int) -> dict[int, float]:
    edges = window_edges(cells)
    print(f"\n══════ {title} ══════")
    print(f"{'구간':<6}{'기간':>12}{'거래':>6}{'순수익':>9}{'거래당무비용':>13}{'비용차감':>10}")
    for index, group in sorted(by_window(cells).items()):
        window = group[0].window
        a, b = window.days_ago(now_ms)
        trades = sum(c.trades for c in group)
        net = statistics.mean([c.net_pct for c in group])
        per_trade = statistics.mean([c.per_trade_gross for c in group])
        print(
            f"{window.label:<6}{f'{b}~{a}일 전':>12}{trades:>6}{net:>8.2f}%"
            f"{per_trade:>12.3f}%{edges[index]:>+9.3f}%"
        )
    return edges


def verdict_line(label: str, values: list[float]) -> None:
    result = stability.assess(values)
    print(f"\n  {label}: **{result.verdict}** — {result.detail}")


async def main_async(args: argparse.Namespace) -> None:
    now_ms = int(time.time() * 1000)
    windows = generate(count=args.windows, length_days=args.days, now_ms=now_ms)
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    a, b = windows[-1].days_ago(now_ms)
    print(
        f"구간 {len(windows)}개 × {args.days}일 (최대 {b}일 전까지) · 심볼 {', '.join(symbols)}"
        f" · 왕복 비용 {stability.ROUND_TRIP_COST_PCT}%"
    )

    print("\n[기준선]")
    base_cells = await run(symbols, windows, timeframe=args.timeframe)
    base_edges = report("기준선", base_cells, now_ms)
    verdict_line("기준선이 비용을 넘는가", list(base_edges.values()))

    if not args.variant:
        return

    overrides = parse_overrides(args.variant)
    print(f"\n[변형] {overrides}")
    variant_cells = await run(symbols, windows, overrides, timeframe=args.timeframe)
    variant_edges = report(f"변형 {overrides}", variant_cells, now_ms)

    shared = sorted(set(base_edges) & set(variant_edges))
    deltas = [variant_edges[i] - base_edges[i] for i in shared]
    print(f"\n══════ 변형 − 기준선 ══════")
    print(f"{'구간':<6}{'개선폭':>10}")
    for index, delta in zip(shared, deltas):
        print(f"#{index + 1:<5}{delta:>+9.3f}%")
    verdict_line("변형이 기준선보다 나은가", deltas)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="워크포워드 검증 — 변경이 시기를 넘어 유효한지 부호 검정으로 판정한다"
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--windows", type=int, default=12, help="구간 수 (기본 12)")
    parser.add_argument("--days", type=int, default=14, help="구간 길이(일) (기본 14)")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument(
        "--variant",
        default="",
        help="비교할 BacktestParams 재정의. 예: take_profit_pct=3.0,stop_loss_pct=1.5",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
