"""Rule Engine — 기술적 지표 점수(S_Tech) 산출.

prompt.md v2 §3.3 배점표를 그대로 옮긴 것이다. 입력은 "지표 스냅샷 딕셔너리"이고,
거래소별 스냅샷(`Indicators.as_dict()`)과 글로벌 거래량 가중 평균 스냅샷
(`MarketManager.aggregate()`) 양쪽을 같은 키 이름으로 읽는다. 그래서 §3.2 의
거래소별 점수와 §3.3 의 글로벌 점수가 **같은 규칙**으로 나온다.

배점 (기준점 + 가감점, 합산 후 clamp):

    기준점                    40점    ← §3.3 에 없다. 아래 "기준점" 참고
    RSI                      0~20점  (30 이하 100 / 70 이상 0 / 50 부근 50 → ×0.2)
    MACD 골든크로스            +15점
    단기 정배열(MA5>MA20>MA60) +15점
    볼린저 하단 이탈 후 복귀    +10점 / 상단 돌파 지속 -10점
    스토캐스틱 %K 상향 돌파     +10점 / 80 이상 과매수 -10점
    ADX 25 이상               추세 방향에 맞춰 ±10점
    CCI -100 이하에서 반등     +10점
    거래량 30% 이상 급증       +10점
    매수 호가 우위(>15%)       +10점

상한 100 은 §3.3 의 `min(sum, 100)` 이고, 하한 0 은 우리 쪽 제약이다
(`ai_signals.tech_score` 가 TINYINT UNSIGNED 라 음수를 저장할 수 없다).

### 기준점 40점 (2026-09-03 결정, prompt.md §3.3 개정 필요)

§3.3 배점표는 가점이 +110, 감점이 -30 으로 상승 쪽에 치우쳐 있다. 그래서 아무 일도
일어나지 않은 시장의 점수가 50 이 아니라 10 이 된다. 반면 방향을 나누는 §3.2 의
임계값(60 이상 BUY / 40 이하 SELL)은 점수가 50 에 중심을 둔다고 가정한다. 둘을 그대로
합치면 **조용한 장에서 전 거래소가 SELL 진영으로 몰려 합의 100% 짜리 STRONG_SELL 이
계속 나가고, AI 호출 필터(30 이하)에도 걸려 비용까지 나간다.**

그래서 시작점을 40 으로 올려 중립 상태가 정확히 50 점이 되게 한다(40 + RSI 중립 10).
§3.3 의 가감점 숫자와 clamp 규칙은 하나도 바꾸지 않았다 — 자를 옮긴 것이 아니라
눈금의 0 을 맞춘 것이다.

점수만 내지 않고 항목별 내역(`items`)을 함께 돌려준다. "왜 이 점수인가"를 UI 와
`data_sources_json` 에 그대로 실을 수 있어야 하기 때문이다 (README §4 "왜"를 보여준다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from trading_engine.indicators.calculator import (
    BB_ABOVE_UPPER,
    BB_BELOW_LOWER,
    TREND_BEARISH,
    TREND_BULLISH,
)

# --- 배점표 상수 (prompt.md v2 §3.3) ----------------------------------------
# 기준점. 이 값 + RSI 중립 점수(10) = 50 이 되어야 §3.2 의 60/40 임계값과 맞는다.
BASE_SCORE = 40.0
RSI_WEIGHT = 20.0
RSI_OVERSOLD = 30.0  # 이하이면 만점
RSI_OVERBOUGHT = 70.0  # 이상이면 0점
MACD_CROSS_POINTS = 15.0
MA_TREND_POINTS = 15.0
BOLLINGER_REENTRY_POINTS = 10.0
BOLLINGER_BREAKOUT_PENALTY = -10.0
STOCH_CROSS_POINTS = 10.0
STOCH_OVERBOUGHT_PENALTY = -10.0
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
ADX_POINTS = 10.0
ADX_TREND_MIN = 25.0
CCI_REBOUND_POINTS = 10.0
CCI_OVERSOLD = -100.0
VOLUME_SURGE_POINTS = 10.0
VOLUME_SURGE_MIN = 0.30  # 직전 봉 대비 +30%
IMBALANCE_POINTS = 10.0
IMBALANCE_MIN = 0.15  # 매수 잔량 우위 15%

SCORE_MIN = 0.0
SCORE_MAX = 100.0

# --- AI 호출 필터 (prompt.md v2 [Step 2] 요구사항 3) -------------------------
AI_TECH_HIGH = 70.0
AI_TECH_LOW = 30.0
AI_MIN_CONSENSUS_PCT = 50.0


@dataclass(frozen=True)
class ScoreItem:
    """배점표 한 줄. 0점이어도 "왜 0점인지" 남기기 위해 버리지 않는다."""

    key: str
    points: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "points": round(self.points, 2), "detail": self.detail}


@dataclass(frozen=True)
class TechScore:
    score: float  # clamp 후 0~100
    raw_sum: float  # clamp 전 합계
    items: tuple[ScoreItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "raw_sum": round(self.raw_sum, 2),
            "items": [item.as_dict() for item in self.items],
        }


def rsi_points(rsi: float | None) -> tuple[float, str]:
    """RSI 를 0~20점으로. 30 이하 만점, 70 이상 0점, 사이는 선형.

    값이 없으면 0점이 아니라 중립(10점)이다. 워밍업 중이라는 이유로 하락 쪽으로
    치우치면 안 된다.
    """
    if rsi is None:
        return RSI_WEIGHT / 2, "RSI 없음 → 중립"
    if rsi <= RSI_OVERSOLD:
        normalized = 100.0
    elif rsi >= RSI_OVERBOUGHT:
        normalized = 0.0
    else:
        span = RSI_OVERBOUGHT - RSI_OVERSOLD
        normalized = (RSI_OVERBOUGHT - rsi) / span * 100.0
    return normalized * RSI_WEIGHT / 100.0, f"RSI {rsi:.1f} → {normalized:.0f}/100"


def bollinger_reentry(
    prev_close: float | None,
    prev_lower: float | None,
    close: float | None,
    lower: float | None,
) -> bool:
    """직전 봉은 하단 밖에서 닫히고 이번 봉은 밴드 안으로 들어왔는지."""
    if None in (prev_close, prev_lower, close, lower):
        return False
    return prev_close < prev_lower and close >= lower


def bollinger_breakout_sustained(
    prev_close: float | None,
    prev_upper: float | None,
    close: float | None,
    upper: float | None,
) -> bool:
    """두 봉 연속 상단 위에서 닫혔는지. '돌파 지속'은 한 봉만 봐서는 알 수 없다."""
    if None in (prev_close, prev_upper, close, upper):
        return False
    return prev_close > prev_upper and close > upper


def stochastic_bullish_cross(
    prev_k: float | None, prev_d: float | None, k: float | None, d: float | None
) -> bool:
    """과매도 구간(%K 20 이하)에서 %K 가 %D 를 상향 돌파했는지."""
    if None in (prev_k, prev_d, k, d):
        return False
    return prev_k <= STOCH_OVERSOLD and prev_k <= prev_d and k > d


def cci_rebound(prev_cci: float | None, cci: float | None) -> bool:
    """직전 봉이 -100 이하였고 이번 봉에 위로 돌아섰는지."""
    if prev_cci is None or cci is None:
        return False
    return prev_cci <= CCI_OVERSOLD and cci > prev_cci


def should_request_ai(
    tech_score: float, consensus_pct: float, sample_sufficient: bool = True
) -> bool:
    """OpenAI 를 호출할지. (prompt.md v2 [Step 2] 요구사항 3)

    Tech 70 이상 **또는** 30 이하 (= 방향성이 뚜렷한 신호)이고,
    동시에 Consensus 50% 이상이어야 한다.

    표본이 3개 미만이면 어차피 HOLD 로 강등되므로(§3.2 4항) 호출하지 않는다.
    돈을 쓰고도 결과를 버리게 된다.
    """
    if not sample_sufficient:
        return False
    if consensus_pct < AI_MIN_CONSENSUS_PCT:
        return False
    return tech_score >= AI_TECH_HIGH or tech_score <= AI_TECH_LOW


class RuleEngine:
    """지표 스냅샷 → S_Tech. 상태를 두지 않으므로 인스턴스를 공유해도 된다."""

    def score(self, snapshot: Mapping[str, Any]) -> TechScore:
        # 내역의 합이 곧 점수여야 한다. 기준점도 한 줄로 남긴다.
        items: list[ScoreItem] = [ScoreItem("base", BASE_SCORE, "기준점 (중립 50점 정렬)")]

        points, detail = rsi_points(_number(snapshot.get("rsi")))
        items.append(ScoreItem("rsi", points, detail))

        golden = bool(snapshot.get("macd_golden_cross"))
        items.append(
            ScoreItem(
                "macd_golden_cross",
                MACD_CROSS_POINTS if golden else 0.0,
                "골든크로스" if golden else "교차 없음",
            )
        )

        trend = snapshot.get("ma_trend")
        items.append(
            ScoreItem(
                "ma_trend",
                MA_TREND_POINTS if trend == TREND_BULLISH else 0.0,
                f"MA 배열 {trend}",
            )
        )

        items.append(self._bollinger_item(snapshot))
        items.append(self._stochastic_item(snapshot))
        items.append(self._adx_item(snapshot, trend))
        items.append(self._cci_item(snapshot))
        items.append(self._volume_item(snapshot))
        items.append(self._orderbook_item(snapshot))

        raw_sum = sum(item.points for item in items)
        return TechScore(
            score=min(max(raw_sum, SCORE_MIN), SCORE_MAX),
            raw_sum=raw_sum,
            items=tuple(items),
        )

    # --- 항목별 -------------------------------------------------------------

    def _bollinger_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        prev_close = _number(snapshot.get("prev_close"))
        close = _number(snapshot.get("close"))
        if bollinger_reentry(
            prev_close,
            _number(snapshot.get("prev_bb_lower")),
            close,
            _number(snapshot.get("bb_lower")),
        ):
            return ScoreItem("bollinger", BOLLINGER_REENTRY_POINTS, "하단 이탈 후 복귀")
        if bollinger_breakout_sustained(
            prev_close,
            _number(snapshot.get("prev_bb_upper")),
            close,
            _number(snapshot.get("bb_upper")),
        ):
            return ScoreItem("bollinger", BOLLINGER_BREAKOUT_PENALTY, "상단 돌파 지속")
        position = snapshot.get("bollinger_position") or "판정 불가"
        if position == BB_BELOW_LOWER:
            position = "하단 이탈 (복귀 대기)"
        elif position == BB_ABOVE_UPPER:
            position = "상단 돌파 (첫 봉)"
        return ScoreItem("bollinger", 0.0, str(position))

    def _stochastic_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        k = _number(snapshot.get("stochastic_k"))
        d = _number(snapshot.get("stochastic_d"))
        if stochastic_bullish_cross(
            _number(snapshot.get("prev_stochastic_k")),
            _number(snapshot.get("prev_stochastic_d")),
            k,
            d,
        ):
            return ScoreItem("stochastic", STOCH_CROSS_POINTS, "과매도에서 %K 상향 돌파")
        if k is not None and k >= STOCH_OVERBOUGHT:
            return ScoreItem("stochastic", STOCH_OVERBOUGHT_PENALTY, f"과매수 %K {k:.1f}")
        return ScoreItem("stochastic", 0.0, "해당 없음" if k is None else f"%K {k:.1f}")

    def _adx_item(self, snapshot: Mapping[str, Any], trend: Any) -> ScoreItem:
        """ADX 는 추세의 '세기'만 알려주고 방향은 모른다.

        그래서 §3.3 의 "방향에 맞춰 ±10점"은 MA 배열이 정한 방향에 세기를 곱하는 것으로
        읽는다. 배열이 혼조면 강화할 방향이 없으므로 0점이다.
        """
        adx = _number(snapshot.get("adx"))
        if adx is None or adx < ADX_TREND_MIN:
            return ScoreItem(
                "adx", 0.0, "추세 약함" if adx is not None else "ADX 없음"
            )
        if trend == TREND_BULLISH:
            return ScoreItem("adx", ADX_POINTS, f"상승 추세 강함 ADX {adx:.1f}")
        if trend == TREND_BEARISH:
            return ScoreItem("adx", -ADX_POINTS, f"하락 추세 강함 ADX {adx:.1f}")
        return ScoreItem("adx", 0.0, f"ADX {adx:.1f} 이나 방향 혼조")

    def _cci_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        prev_cci = _number(snapshot.get("prev_cci"))
        cci = _number(snapshot.get("cci"))
        if cci_rebound(prev_cci, cci):
            return ScoreItem("cci", CCI_REBOUND_POINTS, f"-100 이하에서 반등 ({cci:.0f})")
        return ScoreItem("cci", 0.0, "해당 없음" if cci is None else f"CCI {cci:.0f}")

    def _volume_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        rate = _number(snapshot.get("volume_change_rate"))
        if rate is not None and rate >= VOLUME_SURGE_MIN:
            return ScoreItem("volume", VOLUME_SURGE_POINTS, f"거래량 +{rate * 100:.0f}%")
        return ScoreItem(
            "volume", 0.0, "해당 없음" if rate is None else f"거래량 {rate * 100:+.0f}%"
        )

    def _orderbook_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        imbalance = _number(snapshot.get("orderbook_imbalance"))
        if imbalance is not None and imbalance > IMBALANCE_MIN:
            return ScoreItem(
                "orderbook", IMBALANCE_POINTS, f"매수 우위 {imbalance * 100:.0f}%"
            )
        return ScoreItem(
            "orderbook",
            0.0,
            "호가 없음" if imbalance is None else f"불균형 {imbalance * 100:+.0f}%",
        )


def _number(value: Any) -> float | None:
    """bool 은 숫자가 아니다. NaN 도 값으로 치지 않는다."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number
