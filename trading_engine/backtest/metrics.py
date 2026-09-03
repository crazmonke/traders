"""백테스트 성과 지표. (prompt.md v2 [Step 4] 요구사항 5)

    총 수익률(%) / 승률(%) / Total Trades / 평균 수익·손실 비율(손익비) / MDD(%)

거래 목록과 자본 곡선만 받는 순수 함수다. 시세도 DB도 모른다 — 이 파일이 백테스트에서
가장 자주 의심받을 부분이라, 입력만 주면 손으로 검산할 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class Trade:
    """청산까지 끝난 거래 한 건. 미청산 포지션은 지표에 넣지 않는다."""

    symbol: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    quantity: float
    entry_cost: float  # 수수료·슬리피지 포함 실제 지출
    exit_proceeds: float  # 수수료·슬리피지 차감 실제 수령
    exit_reason: str

    @property
    def pnl(self) -> float:
        return self.exit_proceeds - self.entry_cost

    @property
    def return_pct(self) -> float:
        return 0.0 if self.entry_cost <= 0 else self.pnl / self.entry_cost * 100.0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "entry_price": round(self.entry_price, 8),
            "exit_price": round(self.exit_price, 8),
            "pnl": round(self.pnl, 4),
            "return_pct": round(self.return_pct, 4),
            "exit_reason": self.exit_reason,
        }


@dataclass(frozen=True)
class Metrics:
    initial_capital: float
    final_capital: float
    total_return_pct: float
    win_rate: float
    total_trades: int
    avg_profit_loss_ratio: float | None
    """평균 수익 ÷ 평균 손실(절댓값). 손실 거래가 없으면 None —
    0 이나 999 로 채우면 "손실이 없었다"가 "손익비가 0"으로 읽힌다."""

    mdd: float
    wins: int
    losses: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": round(self.initial_capital, 2),
            "final_capital": round(self.final_capital, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "win_rate": round(self.win_rate, 2),
            "total_trades": self.total_trades,
            "avg_profit_loss_ratio": (
                None
                if self.avg_profit_loss_ratio is None
                else round(self.avg_profit_loss_ratio, 2)
            ),
            "mdd": round(self.mdd, 2),
            "wins": self.wins,
            "losses": self.losses,
        }


def max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    """최고점 대비 최대 낙폭(%). 양수로 돌려준다.

    자본 곡선은 거래 시점만이 아니라 **평가액 기준**으로 넣어야 의미가 있다.
    거래 종료 시점만 찍으면 보유 중에 겪은 낙폭이 통째로 빠진다.
    """
    peak = float("-inf")
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            drawdown = (peak - value) / peak * 100.0
            worst = max(worst, drawdown)

    return worst


def summarize(
    trades: Sequence[Trade], equity_curve: Sequence[float], initial_capital: float
) -> Metrics:
    wins = [trade for trade in trades if trade.is_win]
    losses = [trade for trade in trades if not trade.is_win]

    final_capital = equity_curve[-1] if equity_curve else initial_capital
    total_return_pct = (
        0.0
        if initial_capital <= 0
        else (final_capital - initial_capital) / initial_capital * 100.0
    )

    ratio: float | None = None
    if wins and losses:
        avg_win = sum(trade.pnl for trade in wins) / len(wins)
        avg_loss = abs(sum(trade.pnl for trade in losses) / len(losses))
        ratio = avg_win / avg_loss if avg_loss > 0 else None

    return Metrics(
        initial_capital=initial_capital,
        final_capital=final_capital,
        total_return_pct=total_return_pct,
        win_rate=len(wins) / len(trades) * 100.0 if trades else 0.0,
        total_trades=len(trades),
        avg_profit_loss_ratio=ratio,
        mdd=max_drawdown_pct(equity_curve),
        wins=len(wins),
        losses=len(losses),
    )
