"""백테스트 엔진 — 과거 캔들을 재생해 신호를 만들고 매매를 시뮬레이션한다.

**신호 산출은 운영과 같은 코드를 쓴다.** `SignalEngine`/`RuleEngine`/`consensus` 를 그대로
호출한다. 백테스트가 자기만의 신호 로직을 따로 가지면, 잘 나온 백테스트 결과가 실제로
운영에서 재현되지 않는다.

### AI 점수는 넣지 않는다

과거 시점의 AI 응답은 존재하지 않고, 봉마다 새로 호출하면 (실측 기준) 한 번 돌릴 때마다
수천 건의 유료 호출이 난다. 그래서 백테스트의 Final Score 는 **AI 없이 가중치를 재분배한
값**이다 — 운영에서도 게이트에 걸리지 않은 신호가 이 경로를 탄다(`signal_engine` 참고).
즉 이 백테스트는 **룰 엔진의 성능**을 재는 것이고, "신호 검증용"이라는 이름과 맞는다.

### 매매 조건 (prompt.md v2 [Step 4] 요구사항 3)

스펙은 "매수: Final Score >= 80 / 매도: Final Score <= 30" 으로 적혀 있다. 이는 Final Score 를
0~100 의 **강세 점수**로 읽은 것인데, 우리 구현은 방향 정합 후의 **확신도**다
(ROADMAP "Step 2-a 이번에 정한 것" 2번). 같은 뜻이 되도록 등급으로 옮긴다:

    Final >= 80  → STRONG_BUY                 (진입)
    Final <= 30  → SELL / STRONG_SELL         (청산)

### 익절·손절 폭 (2026-09-03 조합 탐색으로 스펙에서 변경)

스펙의 익절 +1.5% / 손절 -1.0% 는 손익비 **1.5:1** 이라, 비용을 빼고 나면 승률이 40% 를
넘어야 본전이다. 실측 승률은 그 설정에서 29~48% 였고 결과는 전 심볼 손실이었다.

1시간봉 90일 탐색에서 **손익비 2:1 로 넓힐수록 결과가 단조롭게 좋아졌다**(BTC 기준
1.5/1.0 → -9.4% / 3.0/1.5 → +6.9% / 5.0/2.5 → +10.5% / 8.0/4.0 → +20.1%, 세 임계값 모두
같은 방향). 그래서 기본값을 **5.0 / 2.5** 로 옮겼다.

8.0/4.0 이 더 좋았지만 탐색 격자의 **가장자리** 값이라 채택하지 않았다 — 가장자리에서
가장 좋은 값은 대개 더 넓히면 더 좋아지는지 확인되지 않은 값이다. 5.0/2.5 는 BTC 세
임계값 전부와 ETH 기본 임계값에서 양수였다.

**진입 임계값은 기본값을 올리지 않았다.** 올리면 거래 수는 확실히 줄지만 수익은
일관되게 좋아지지 않았고(BTC 는 비슷, ETH 는 크게 나빠짐), 무엇보다 **운영 등급과
어긋난다** — 운영은 Final 80 이상을 STRONG_BUY 로 내보내므로, 백테스트만 85 를 요구하면
"운영은 사는데 백테스트는 안 사는" 신호가 생긴다. 임계값을 실제로 올리려면
`STRONG_THRESHOLD` 를 함께 올리거나 Step 5 에 매매 전용 임계값을 둬야 한다.

### 미래를 보지 않기 위한 규칙

봉 종가로 계산한 신호는 **그 봉이 닫힌 뒤에야** 알 수 있다. 그래서 체결은 항상 다음 봉의
시가에서 일어난다. 한 봉 안에서 익절가와 손절가에 모두 닿았으면 **손절이 먼저 닿았다고
본다** — OHLC 만으로는 순서를 알 수 없고, 유리한 쪽을 가정하면 백테스트가 부풀려진다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from trading_engine.backtest import metrics as metrics_mod
from trading_engine.backtest.costs import CostModel
from trading_engine.backtest.metrics import Metrics, Trade
from trading_engine.indicators import calculator
from trading_engine.market.exchange_registry import get_spec
from trading_engine.market.market_manager import MarketManager
from trading_engine.strategy.consensus import BUY
from trading_engine.strategy.signal_engine import (
    SIGNAL_SELL,
    SIGNAL_STRONG_BUY,
    SIGNAL_STRONG_SELL,
    STRONG_THRESHOLD,
    SignalEngine,
    SignalEvaluation,
)

log = logging.getLogger(__name__)

# 운영 수집기와 같은 봉 수를 본다 (`exchange_feed.CANDLE_LIMIT`).
# 다르게 잡으면 백테스트와 운영의 지표가 서로 다른 창을 보게 된다.
WINDOW = 100

ENTRY_SIGNALS = (SIGNAL_STRONG_BUY,)
EXIT_SIGNALS = (SIGNAL_SELL, SIGNAL_STRONG_SELL)

# 이보다 평가 봉이 적으면 결과를 통계로 읽을 수 없다. 실측으로 주봉은 심볼당 거래가
# 0~1건, 월봉은 데이터 자체가 110봉뿐이라 워밍업(100봉)을 빼면 10봉만 남는다.
MIN_MEANINGFUL_BARS = 200

EXIT_TAKE_PROFIT = "TAKE_PROFIT"
EXIT_STOP_LOSS = "STOP_LOSS"
EXIT_SIGNAL = "SIGNAL"
EXIT_END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True)
class BacktestParams:
    symbol: str
    reference_exchange: str
    # 봉 간격. 같은 구간이라도 5분봉과 1시간봉은 완전히 다른 결과가 나온다.
    # 기록에 없으면 저장된 결과를 재현할 수 없다.
    timeframe: str = "5m"
    initial_capital: float = 1_000_000.0
    # 익절·손절 기본값. **스펙(+1.5% / -1.0%)에서 바꿨다** — 2026-09-03 조합 탐색 결과.
    # 자세한 근거는 아래 "익절·손절 폭" 주석과 ROADMAP 참고.
    take_profit_pct: float = 5.0
    stop_loss_pct: float = 2.5
    # 진입에 요구하는 Final Score. 기본값은 운영 등급의 STRONG_BUY 기준과 같다
    # (`signal_engine.STRONG_THRESHOLD`). 여기서 올려도 **운영 등급 표기는 바뀌지 않는다** —
    # 백테스트로 임계값을 탐색한 뒤에 운영에 반영할지 따로 결정하기 위해 분리했다.
    min_final_score: float = STRONG_THRESHOLD
    # 한 번에 자본의 몇 %를 넣는가. 100 이면 전액.
    position_size_pct: float = 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "reference_exchange": self.reference_exchange,
            "timeframe": self.timeframe,
            "initial_capital": self.initial_capital,
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "min_final_score": self.min_final_score,
            "position_size_pct": self.position_size_pct,
            "window": WINDOW,
            "entry_signals": list(ENTRY_SIGNALS),
            "exit_signals": list(EXIT_SIGNALS),
            "ai_score_included": False,
        }


@dataclass(frozen=True)
class BacktestResult:
    params: BacktestParams
    cost_model: CostModel
    metrics: Metrics
    trades: tuple[Trade, ...]
    equity_curve: tuple[float, ...]
    bars_evaluated: int
    start_ts: int | None
    end_ts: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params.as_dict(),
            "cost_model": {
                "label": self.cost_model.label,
                "fee_rate": self.cost_model.fee_rate,
                "slippage_rate": self.cost_model.slippage_rate,
            },
            "metrics": self.metrics.as_dict(),
            "bars_evaluated": self.bars_evaluated,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "trades": [trade.as_dict() for trade in self.trades],
        }


@dataclass
class _Position:
    entry_ts: int
    entry_price: float
    quantity: float
    entry_cost: float


class Backtester:
    """거래소별 캔들 + 기준 가격 시계열을 받아 재생한다.

    입력을 직접 받으므로 네트워크도 DB 도 건드리지 않는다. 같은 입력이면 항상 같은
    결과가 나온다(재현성 테스트가 이 성질을 고정한다).
    """

    def __init__(self, params: BacktestParams, cost_model: CostModel) -> None:
        self._params = params
        self._costs = cost_model

    def compute_signals(
        self,
        exchange_candles: Mapping[str, Sequence[dict[str, Any]]],
        price_series: Sequence[dict[str, Any]],
    ) -> dict[int, SignalEvaluation]:
        """봉 인덱스 → 그 봉이 닫힌 시점의 평가.

        **익절·손절·진입 임계값과 무관하다.** 그래서 한 번 계산해 두면 여러 조합을
        같은 신호 위에서 비교할 수 있다. 지표 계산이 백테스트 비용의 대부분이라,
        조합 탐색에서 이 분리가 없으면 같은 계산을 수십 번 반복하게 된다.
        """
        usable, length = self._usable(exchange_candles, price_series)
        if not usable:
            return {}

        manager = MarketManager(store=None)
        signals = SignalEngine(manager, store=None)
        out: dict[int, SignalEvaluation] = {}

        for index in range(WINDOW, length - 1):
            evaluation = self._evaluate_at(manager, signals, usable, index)
            if evaluation is not None:
                out[index] = evaluation

        return out

    def _usable(
        self,
        exchange_candles: Mapping[str, Sequence[dict[str, Any]]],
        price_series: Sequence[dict[str, Any]],
    ) -> tuple[dict[str, Sequence[dict[str, Any]]], int]:
        usable = {
            code: candles
            for code, candles in exchange_candles.items()
            if len(candles) > WINDOW
        }
        if not usable or len(price_series) <= WINDOW:
            log.warning("백테스트할 봉이 부족하다 (symbol=%s)", self._params.symbol)
            return {}, 0

        return usable, min(min(len(c) for c in usable.values()), len(price_series))

    def run(
        self,
        exchange_candles: Mapping[str, Sequence[dict[str, Any]]],
        price_series: Sequence[dict[str, Any]],
        signals_by_index: Mapping[int, SignalEvaluation] | None = None,
    ) -> BacktestResult:
        """
        `exchange_candles` — 거래소 코드 → 캔들 리스트(오래된 순). 신호 산출에 쓴다.
        `price_series` — 체결에 쓸 기준 OHLC(오래된 순). 길이와 순서가 캔들과 맞아야 한다.
        `signals_by_index` — `compute_signals()` 결과를 재사용할 때 넘긴다.
        """
        usable, length = self._usable(exchange_candles, price_series)
        if not usable:
            return self._empty_result()

        manager = MarketManager(store=None)
        signals = SignalEngine(manager, store=None)

        trades: list[Trade] = []
        equity_curve: list[float] = []
        cash = self._params.initial_capital
        position: _Position | None = None
        bars = 0

        # 마지막 봉의 신호는 체결할 다음 봉이 없으므로 length-1 까지만 평가한다.
        for index in range(WINDOW, length - 1):
            if signals_by_index is None:
                evaluation = self._evaluate_at(manager, signals, usable, index)
            else:
                evaluation = signals_by_index.get(index)
            signal_type = None if evaluation is None else evaluation.signal_type
            bars += 1

            current = price_series[index]
            next_bar = price_series[index + 1]

            if position is not None:
                exit_info = self._find_exit(position, current, next_bar, signal_type)
                if exit_info is not None:
                    exit_price, reason, exit_ts = exit_info
                    proceeds = self._costs.sell_proceeds(exit_price, position.quantity)
                    trades.append(
                        Trade(
                            symbol=self._params.symbol,
                            entry_ts=position.entry_ts,
                            exit_ts=exit_ts,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            quantity=position.quantity,
                            entry_cost=position.entry_cost,
                            exit_proceeds=proceeds,
                            exit_reason=reason,
                        )
                    )
                    cash += proceeds
                    position = None
            elif self._is_entry(evaluation):
                position = self._open(cash, next_bar)
                if position is not None:
                    cash -= position.entry_cost

            equity_curve.append(self._equity(cash, position, current))

        if position is not None:
            trades.append(self._close_at_end(position, price_series[length - 1]))
            cash += trades[-1].exit_proceeds
            equity_curve.append(cash)

        if bars and bars < MIN_MEANINGFUL_BARS:
            log.warning(
                "평가 봉이 %d개뿐이라 결과를 통계로 읽으면 안 된다 (%s %s)",
                bars,
                self._params.symbol,
                self._params.timeframe,
            )

        return BacktestResult(
            params=self._params,
            cost_model=self._costs,
            metrics=metrics_mod.summarize(
                trades, equity_curve, self._params.initial_capital
            ),
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            bars_evaluated=bars,
            start_ts=int(price_series[WINDOW]["ts"]),
            end_ts=int(price_series[length - 1]["ts"]),
        )

    # --- 신호 -----------------------------------------------------------------

    def _is_entry(self, evaluation: SignalEvaluation | None) -> bool:
        """진입 조건. 기본값에서는 `STRONG_BUY` 와 같고, 임계값을 올리면 더 엄격해진다."""
        if evaluation is None:
            return False
        return (
            evaluation.direction == BUY
            and evaluation.final_score >= self._params.min_final_score
        )

    def _evaluate_at(
        self,
        manager: MarketManager,
        signals: SignalEngine,
        exchange_candles: Mapping[str, Sequence[dict[str, Any]]],
        index: int,
    ) -> SignalEvaluation | None:
        """이 봉이 닫힌 시점의 평가. 운영과 같은 경로로 낸다."""
        for code, candles in exchange_candles.items():
            window = candles[index - WINDOW : index + 1]
            symbol = _exchange_symbol(code, self._params.symbol)
            manager.record_indicators(code, symbol, calculator.compute(symbol, window))
            last = window[-1]
            # 가중치는 운영에서 24시간 거래대금이다. 과거 값을 알 수 없어 그 봉의
            # 거래대금(종가 × 거래량)으로 대신한다. 상대 비중은 비슷하게 유지된다.
            manager.record_ticker(
                code, symbol, {"quote_volume_24h": last["close"] * last["volume"]}
            )

        return signals.evaluate(self._params.symbol)

    # --- 매매 -----------------------------------------------------------------

    def _open(self, cash: float, next_bar: Mapping[str, Any]) -> _Position | None:
        """다음 봉 시가에 진입한다. 종가로 낸 신호를 그 봉에서 체결하면 미래를 보는 것이다."""
        budget = cash * self._params.position_size_pct / 100.0
        entry_price = float(next_bar["open"])
        if budget <= 0 or entry_price <= 0:
            return None

        # 수수료까지 포함해 예산 안에 들어오도록 수량을 잡는다.
        unit_cost = self._costs.buy_cost(entry_price, 1.0)
        quantity = budget / unit_cost
        if quantity <= 0:
            return None

        return _Position(
            entry_ts=int(next_bar["ts"]),
            entry_price=entry_price,
            quantity=quantity,
            entry_cost=self._costs.buy_cost(entry_price, quantity),
        )

    def _find_exit(
        self,
        position: _Position,
        current: Mapping[str, Any],
        next_bar: Mapping[str, Any],
        signal_type: str | None,
    ) -> tuple[float, str, int] | None:
        """청산 사유와 가격. 없으면 None.

        익절·손절은 **이번 봉 안에서** 판정한다(보유 중이므로 실시간으로 걸린다).
        신호 청산은 이번 봉 종가로 판단하고 다음 봉 시가에 체결한다.
        """
        entry = position.entry_price
        take_profit = entry * (1.0 + self._params.take_profit_pct / 100.0)
        stop_loss = entry * (1.0 - self._params.stop_loss_pct / 100.0)

        hit_stop = float(current["low"]) <= stop_loss
        hit_target = float(current["high"]) >= take_profit

        # 둘 다 닿았으면 손절이 먼저였다고 본다. OHLC 로는 순서를 알 수 없고,
        # 유리한 쪽을 가정하면 백테스트가 부풀려진다.
        if hit_stop:
            return stop_loss, EXIT_STOP_LOSS, int(current["ts"])
        if hit_target:
            return take_profit, EXIT_TAKE_PROFIT, int(current["ts"])
        if signal_type in EXIT_SIGNALS:
            return float(next_bar["open"]), EXIT_SIGNAL, int(next_bar["ts"])

        return None

    def _close_at_end(
        self, position: _Position, last_bar: Mapping[str, Any]
    ) -> Trade:
        """데이터가 끝났는데 보유 중이면 마지막 종가로 청산해 지표에 포함시킨다.

        미청산으로 남기면 그 손익이 승률·손익비에서 통째로 빠져 결과가 좋아 보인다.
        """
        exit_price = float(last_bar["close"])
        return Trade(
            symbol=self._params.symbol,
            entry_ts=position.entry_ts,
            exit_ts=int(last_bar["ts"]),
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_cost=position.entry_cost,
            exit_proceeds=self._costs.sell_proceeds(exit_price, position.quantity),
            exit_reason=EXIT_END_OF_DATA,
        )

    def _equity(
        self, cash: float, position: _Position | None, bar: Mapping[str, Any]
    ) -> float:
        """평가액. 보유 중에도 매 봉 기록해야 MDD 가 실제 낙폭을 잡는다."""
        if position is None:
            return cash
        return cash + self._costs.sell_proceeds(float(bar["close"]), position.quantity)

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            params=self._params,
            cost_model=self._costs,
            metrics=metrics_mod.summarize([], [], self._params.initial_capital),
            trades=(),
            equity_curve=(),
            bars_evaluated=0,
            start_ts=None,
            end_ts=None,
        )


def _exchange_symbol(code: str, base: str) -> str:
    """거래소 표기 심볼. 레지스트리에 없는 코드는 USDT 로 가정한다(테스트용 가짜 거래소)."""
    try:
        return get_spec(code).symbol(base)
    except KeyError:
        return f"{base}/USDT"
