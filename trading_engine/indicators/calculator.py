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
ATR_LENGTH = 14
DAY_MS = 24 * 60 * 60 * 1000

# 각 지표가 값을 내려면 필요한 최소 봉 수
MIN_RSI_CANDLES = RSI_LENGTH + 1
MIN_MACD_CANDLES = MACD_SLOW + MACD_SIGNAL
MIN_BBANDS_CANDLES = BBANDS_LENGTH
MIN_STOCH_CANDLES = STOCH_K + STOCH_D
MIN_ADX_CANDLES = ADX_LENGTH * 2  # DM 평활에 워밍업이 한 주기 더 필요하다
MIN_CCI_CANDLES = CCI_LENGTH
MIN_ATR_CANDLES = ATR_LENGTH + 1  # 첫 TR 은 직전 종가가 있어야 나온다

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
    macd_dead_cross: bool
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
    atr: float | None
    # 거래량 가중 평균가와 괴리율. 참고 지표이며 배점에는 들어가지 않는다(2026-09-03).
    vwap: float | None
    vwap_divergence: float | None
    # 직전 봉 값. Step 2 배점표에는 "하단 이탈 후 복귀", "%K 가 %D 를 상향 돌파",
    # "CCI 가 -100 이하에서 반등" 처럼 한 봉 전과 비교해야 판정되는 항목이 있다.
    # 판정 자체(=배점)는 RuleEngine 이 하고, 여기서는 재료만 숫자로 내놓는다.
    prev_close: float | None
    prev_macd: float | None
    prev_macd_signal: float | None
    prev_bb_lower: float | None
    prev_bb_upper: float | None
    prev_stochastic_k: float | None
    prev_stochastic_d: float | None
    prev_cci: float | None
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


def _prev(series: pd.Series | None) -> float | None:
    """마지막에서 두 번째 값(직전 봉). 없거나 NaN 이면 None."""
    if series is None or len(series) < 2:
        return None
    value = series.iloc[-2]
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


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = ATR_LENGTH
) -> pd.Series:
    """ATR = Wilder 평활한 True Range. TR = max(고-저, |고-직전종가|, |저-직전종가|).

    `pandas_ta.atr` 를 쓰지 않는다. 버전에 따라 반환형(Series/DataFrame)과 기본 평활
    방식(RMA/EMA/SMA)이 달라져, 같은 캔들에도 다른 값이 나온다. 이 값은 S_Risk 감점에
    직접 들어가므로 라이브러리 버전에 흔들리지 않게 직접 계산한다. (compute_cci 와 같은 이유)
    """
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # Wilder 평활은 alpha=1/length 인 지수이동평균과 같다.
    return true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def compute_session_vwap(candles: Sequence[dict[str, Any]]) -> float | None:
    """세션 VWAP — UTC 자정 이후 봉만으로 낸 거래량 가중 평균가.

    **트레이딩뷰가 보여주는 VWAP 과 같은 정의**(세션 앵커드)다. 롤링 VWAP 을 먼저
    구현했다가 바꿨다 — 롤링은 사실상 이동평균이라 RSI 와 r=0.895 로 거의 중복이었고,
    세션 앵커드는 r=0.799 로 그나마 낫다(BTC 1h 620표본 실측, 2026-09-03).

    **한계:** 우리는 100봉 창으로 지표를 돌리므로 창을 벗어난 세션 앵커는 볼 수 없다.
    5분봉에서 UTC 하루는 288봉이라, 창에 담긴 부분만으로 계산된다. 트레이딩뷰 값과
    정확히 일치하지 않을 수 있고, 그래서 **배점에 넣지 않고 참고 지표로만 쓴다.**

    거래량이 0이면(휴장·결측) 정의되지 않으므로 None.
    """
    if not candles:
        return None

    last_day = int(candles[-1]["ts"]) // DAY_MS
    session = [c for c in candles if int(c["ts"]) // DAY_MS == last_day]
    volume = sum(float(c["volume"]) for c in session)
    if not session or volume <= 0:
        return None

    weighted = sum(
        (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0 * float(c["volume"])
        for c in session
    )

    return weighted / volume


def vwap_divergence_pct(close: float | None, vwap: float | None) -> float | None:
    """현재가가 VWAP 에서 몇 % 떨어져 있는지. 양수면 VWAP 위(과열 쪽).

    "VWAP Divergence" 라는 이름의 상용 인디케이터가 여럿 있으나 각자 계산이 다르고
    비공개인 것도 많다. 여기 있는 것은 **공개된 표준 정의**(가격과 VWAP 의 괴리율)이며
    특정 상용 스크립트를 옮긴 것이 아니다 — 비공개 스크립트는 재현할 수도 없고
    해서도 안 된다.
    """
    if close is None or vwap is None or vwap <= 0:
        return None

    return (close - vwap) / vwap * 100.0


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


def is_golden_cross(
    prev_macd: float | None,
    prev_signal: float | None,
    macd: float | None,
    signal: float | None,
) -> bool:
    """직전 봉에서 시그널 아래였던 MACD가 이번 봉에 위로 올라섰는지.

    거래소별 스냅샷과 글로벌 가중 평균 스냅샷 양쪽에서 같은 판정을 쓰기 위해
    Series 가 아니라 네 개의 스칼라를 받는다.
    """
    if None in (prev_macd, prev_signal, macd, signal):
        return False
    return bool(prev_macd <= prev_signal and macd > signal)


def detect_golden_cross(macd: pd.Series, signal: pd.Series) -> bool:
    """`is_golden_cross` 의 Series 판. 마지막 두 봉만 본다."""
    return is_golden_cross(_prev(macd), _prev(signal), _last(macd), _last(signal))


def is_dead_cross(
    prev_macd: float | None,
    prev_signal: float | None,
    macd: float | None,
    signal: float | None,
) -> bool:
    """골든크로스의 거울상 — 시그널 위였던 MACD 가 이번 봉에 아래로 내려섰는지.

    **이 판정이 없어서 배점표가 상승 쪽으로 기울어 있었다.** 골든크로스에는 가점을
    주면서 데드크로스에는 줄 감점이 없으니, 하락 근거를 점수로 표현할 방법이
    RSI 과매수(0점)뿐이었다 (`rule_engine` 의 "대칭" 절 참고).
    """
    if None in (prev_macd, prev_signal, macd, signal):
        return False
    return bool(prev_macd >= prev_signal and macd < signal)


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
    golden_cross = dead_cross = False
    mas: dict[int, float | None] = {period: None for period in MA_PERIODS}
    close_price = volume_change = None
    candle_ts = None
    bb_lower = bb_mid = bb_upper = None
    stoch_k = stoch_d = adx_value = cci_value = atr_value = None
    vwap_value = vwap_div = None
    prev_close = prev_macd = prev_macd_signal = None
    prev_bb_lower = prev_bb_upper = None
    prev_stoch_k = prev_stoch_d = prev_cci = None

    if count:
        close = df["close"].astype(float)
        close_price = _last(close)
        prev_close = _prev(close)
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
                prev_macd = _prev(macd_line)
                prev_macd_signal = _prev(signal_line)
                golden_cross = is_golden_cross(
                    prev_macd, prev_macd_signal, macd_value, macd_signal_value
                )
                dead_cross = is_dead_cross(
                    prev_macd, prev_macd_signal, macd_value, macd_signal_value
                )

        for period in MA_PERIODS:
            if count >= period:
                mas[period] = _last(close.rolling(period).mean())

        high = df["high"].astype(float)
        low = df["low"].astype(float)

        if count >= MIN_BBANDS_CANDLES:
            bb = ta.bbands(close, length=BBANDS_LENGTH, std=BBANDS_STD)
            lower_band = _column(bb, "BBL_")
            upper_band = _column(bb, "BBU_")
            bb_lower = _last(lower_band)
            bb_mid = _last(_column(bb, "BBM_"))
            bb_upper = _last(upper_band)
            prev_bb_lower = _prev(lower_band)
            prev_bb_upper = _prev(upper_band)

        if count >= MIN_STOCH_CANDLES:
            stoch = ta.stoch(high, low, close, k=STOCH_K, d=STOCH_D)
            k_line = _column(stoch, "STOCHk_")
            d_line = _column(stoch, "STOCHd_")
            stoch_k = _last(k_line)
            stoch_d = _last(d_line)
            prev_stoch_k = _prev(k_line)
            prev_stoch_d = _prev(d_line)

        if count >= MIN_ADX_CANDLES:
            adx_frame = ta.adx(high, low, close, length=ADX_LENGTH)
            adx_value = _last(_column(adx_frame, "ADX_"))

        if count >= MIN_CCI_CANDLES:
            cci_line = compute_cci(high, low, close, CCI_LENGTH)
            cci_value = _last(cci_line)
            prev_cci = _prev(cci_line)

        if count >= MIN_ATR_CANDLES:
            atr_value = _last(compute_atr(high, low, close, ATR_LENGTH))

        vwap_value = compute_session_vwap(candles)
        vwap_div = vwap_divergence_pct(close_price, vwap_value)

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
        macd_dead_cross=dead_cross,
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
        atr=atr_value,
        vwap=vwap_value,
        vwap_divergence=vwap_div,
        prev_close=prev_close,
        prev_macd=prev_macd,
        prev_macd_signal=prev_macd_signal,
        prev_bb_lower=prev_bb_lower,
        prev_bb_upper=prev_bb_upper,
        prev_stochastic_k=prev_stoch_k,
        prev_stochastic_d=prev_stoch_d,
        prev_cci=prev_cci,
        candle_count=count,
        candle_ts=candle_ts,
    )
