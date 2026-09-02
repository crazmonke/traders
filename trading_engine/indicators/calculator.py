"""기술적 지표 계산.

캔들 리스트(오래된 순)와 호가 스냅샷을 받아 RSI/MACD/MA/호가 불균형을 산출한다.
배점 환산은 하지 않는다. 점수화는 Step 2의 RuleEngine 몫이다.
(prompt.md [Step 1] 요구사항 4 / 3.2절 입력값)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import pandas as pd
import pandas_ta as ta

RSI_LENGTH = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MA_PERIODS = (5, 20, 60)
BBANDS_LENGTH = 20
BBANDS_STD = 2.0
STOCH_K = 14
STOCH_D = 3
ADX_LENGTH = 14
CCI_LENGTH = 20

# 각 지표가 값을 내려면 필요한 최소 봉 수
MIN_RSI_CANDLES = RSI_LENGTH + 1
MIN_MACD_CANDLES = MACD_SLOW + MACD_SIGNAL
MIN_BBANDS_CANDLES = BBANDS_LENGTH
MIN_STOCH_CANDLES = STOCH_K + STOCH_D
MIN_ADX_CANDLES = ADX_LENGTH * 2  # DM 평활에 워밍업이 한 주기 더 필요하다
MIN_CCI_CANDLES = CCI_LENGTH

BB_BELOW_LOWER = "BELOW_LOWER"
BB_LOWER_HALF = "LOWER_HALF"
BB_UPPER_HALF = "UPPER_HALF"
BB_ABOVE_UPPER = "ABOVE_UPPER"

TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"
TREND_MIXED = "mixed"
TREND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Indicators:
    """한 마켓의 지표 스냅샷. 값을 낼 만큼 봉이 안 쌓였으면 None 이다."""

    market: str
    close: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    macd_golden_cross: bool
    ma5: float | None
    ma20: float | None
    ma60: float | None
    ma_trend: str
    bb_lower: float | None
    bb_mid: float | None
    bb_upper: float | None
    bollinger_position: str | None
    stochastic_k: float | None
    stochastic_d: float | None
    adx: float | None
    cci: float | None
    orderbook_imbalance: float | None
    volume_change_rate: float | None
    candle_count: int
    candle_ts: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _last(series: pd.Series | None) -> float | None:
    """마지막 값을 float 로. 워밍업 구간의 NaN 은 None 으로 바꾼다."""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _column(frame: pd.DataFrame | None, prefix: str) -> pd.Series | None:
    """접두사로 컬럼을 찾는다.

    pandas_ta 는 버전마다 접미사가 달라진다 (예: BBL_20_2.0 vs BBL_20_2.0_2.0).
    이름을 통째로 박아두면 라이브러리를 올릴 때 조용히 None 이 된다.
    """
    if frame is None or frame.empty:
        return None
    for name in frame.columns:
        if name.startswith(prefix):
            return frame[name]
    return None


def compute_cci(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = CCI_LENGTH
) -> pd.Series:
    """CCI = (TP - SMA(TP)) / (0.015 * 평균편차). TP = (고가+저가+종가)/3.

    `pandas_ta.cci` 를 쓰지 않는다. 0.4.71b0 에서 부호와 크기가 모두 어긋난 값을
    돌려준다(상승 추세 표본에서 수동 계산 +126.67 vs 라이브러리 -4986.74).
    Step 2 배점에 그대로 들어가는 값이라 직접 계산한다. 2026-09-02 확인.
    """
    typical = (high + low + close) / 3.0
    sma = typical.rolling(length).mean()
    mean_dev = typical.rolling(length).apply(
        lambda window: float(abs(window - window.mean()).mean()), raw=True
    )
    # 평균편차 0 (완전 평탄) 이면 정의되지 않는다. inf 대신 NaN 으로 둔다.
    return (typical - sma) / (0.015 * mean_dev.replace(0.0, pd.NA))


def classify_bollinger_position(
    close: float | None, lower: float | None, mid: float | None, upper: float | None
) -> str | None:
    """현재가가 밴드의 어디에 있는지. ai_signals.bollinger_position ENUM 과 같은 값."""
    if None in (close, lower, mid, upper):
        return None
    if close < lower:
        return BB_BELOW_LOWER
    if close > upper:
        return BB_ABOVE_UPPER
    return BB_LOWER_HALF if close < mid else BB_UPPER_HALF


def to_dataframe(candles: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """오래된 순 캔들 리스트를 OHLCV DataFrame 으로."""
    if not candles:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(list(candles))


def classify_ma_trend(
    ma5: float | None, ma20: float | None, ma60: float | None
) -> str:
    """MA5>MA20>MA60 이면 정배열, 역순이면 역배열, 나머지는 혼조. (3.2절 배점표)"""
    if ma5 is None or ma20 is None or ma60 is None:
        return TREND_UNKNOWN
    if ma5 > ma20 > ma60:
        return TREND_BULLISH
    if ma5 < ma20 < ma60:
        return TREND_BEARISH
    return TREND_MIXED


def compute_orderbook_imbalance(
    total_bid_size: float | None, total_ask_size: float | None
) -> float | None:
    """(매수-매도)/(매수+매도). 양수면 매수 우위, +0.15 초과가 3.2절의 "Imbalance > 15%"."""
    bid = float(total_bid_size or 0.0)
    ask = float(total_ask_size or 0.0)
    total = bid + ask
    if total <= 0:
        return None
    return (bid - ask) / total


def detect_golden_cross(macd: pd.Series, signal: pd.Series) -> bool:
    """직전 봉에서 시그널 아래였던 MACD가 이번 봉에 위로 올라섰는지."""
    if macd is None or signal is None or len(macd) < 2 or len(signal) < 2:
        return False
    prev_macd, curr_macd = macd.iloc[-2], macd.iloc[-1]
    prev_signal, curr_signal = signal.iloc[-2], signal.iloc[-1]
    if any(pd.isna(v) for v in (prev_macd, curr_macd, prev_signal, curr_signal)):
        return False
    return bool(prev_macd <= prev_signal and curr_macd > curr_signal)


def compute_volume_change_rate(volumes: pd.Series) -> float | None:
    """직전 봉 대비 거래량 증감률. 3.2절 "거래량 급증(+30%)" 판정 입력."""
    if volumes is None or len(volumes) < 2:
        return None
    prev = volumes.iloc[-2]
    curr = volumes.iloc[-1]
    if pd.isna(prev) or pd.isna(curr) or prev <= 0:
        return None
    return float((curr - prev) / prev)


def compute(
    market: str,
    candles: Sequence[dict[str, Any]],
    orderbook: dict[str, Any] | None = None,
) -> Indicators:
    """봉이 모자라면 계산 가능한 항목만 채운 스냅샷을 돌려준다."""
    df = to_dataframe(candles)
    count = len(df)

    rsi = macd_value = macd_signal_value = macd_hist = None
    golden_cross = False
    mas: dict[int, float | None] = {period: None for period in MA_PERIODS}
    close_price = volume_change = None
    candle_ts = None
    bb_lower = bb_mid = bb_upper = None
    stoch_k = stoch_d = adx_value = cci_value = None

    if count:
        close = df["close"].astype(float)
        close_price = _last(close)
        candle_ts = int(df["ts"].iloc[-1]) if "ts" in df else None
        volume_change = compute_volume_change_rate(df["volume"].astype(float))

        if count >= MIN_RSI_CANDLES:
            rsi = _last(ta.rsi(close, length=RSI_LENGTH))

        if count >= MIN_MACD_CANDLES:
            macd_df = ta.macd(close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
            if macd_df is not None and not macd_df.empty:
                suffix = f"{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
                macd_line = macd_df[f"MACD_{suffix}"]
                signal_line = macd_df[f"MACDs_{suffix}"]
                macd_value = _last(macd_line)
                macd_signal_value = _last(signal_line)
                macd_hist = _last(macd_df[f"MACDh_{suffix}"])
                golden_cross = detect_golden_cross(macd_line, signal_line)

        for period in MA_PERIODS:
            if count >= period:
                mas[period] = _last(close.rolling(period).mean())

        high = df["high"].astype(float)
        low = df["low"].astype(float)

        if count >= MIN_BBANDS_CANDLES:
            bb = ta.bbands(close, length=BBANDS_LENGTH, std=BBANDS_STD)
            bb_lower = _last(_column(bb, "BBL_"))
            bb_mid = _last(_column(bb, "BBM_"))
            bb_upper = _last(_column(bb, "BBU_"))

        if count >= MIN_STOCH_CANDLES:
            stoch = ta.stoch(high, low, close, k=STOCH_K, d=STOCH_D)
            stoch_k = _last(_column(stoch, "STOCHk_"))
            stoch_d = _last(_column(stoch, "STOCHd_"))

        if count >= MIN_ADX_CANDLES:
            adx_frame = ta.adx(high, low, close, length=ADX_LENGTH)
            adx_value = _last(_column(adx_frame, "ADX_"))

        if count >= MIN_CCI_CANDLES:
            cci_value = _last(compute_cci(high, low, close, CCI_LENGTH))

    orderbook = orderbook or {}
    imbalance = compute_orderbook_imbalance(
        orderbook.get("total_bid_size"), orderbook.get("total_ask_size")
    )

    return Indicators(
        market=market,
        close=close_price,
        rsi=rsi,
        macd=macd_value,
        macd_signal=macd_signal_value,
        macd_hist=macd_hist,
        macd_golden_cross=golden_cross,
        ma5=mas[5],
        ma20=mas[20],
        ma60=mas[60],
        ma_trend=classify_ma_trend(mas[5], mas[20], mas[60]),
        bb_lower=bb_lower,
        bb_mid=bb_mid,
        bb_upper=bb_upper,
        bollinger_position=classify_bollinger_position(
            close_price, bb_lower, bb_mid, bb_upper
        ),
        stochastic_k=stoch_k,
        stochastic_d=stoch_d,
        adx=adx_value,
        cci=cci_value,
        orderbook_imbalance=imbalance,
        volume_change_rate=volume_change,
        candle_count=count,
        candle_ts=candle_ts,
    )
