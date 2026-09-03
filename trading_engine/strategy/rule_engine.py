"""Rule Engine — 기술적 지표 점수(S_Tech) 산출. **배점표 v3 (Step 16 재설계).**

배점 (기준점 ± 가감점, 합산 후 clamp):

    기준점                       50점   ← 중립이 정확히 50
    모멘텀 (RSI 단독)            ±10점
    추세 배열 (MA5>MA20>MA60)    ±15점
    추세 강도 (ADX × 배열 방향)   ±10점
    MACD 교차 (골든/데드)         ±10점
    볼린저 위치 (복귀/돌파지속)     ±5점
    거래량 급증 (추세 방향)         ±5점
    ─────────────────────────────────
    점수 제외 · 참고 표시만:  스토캐스틱, CCI, 호가 불균형, VWAP 이격

### 왜 다시 썼는가 (2026-09-04 실측)

v2 배점표(§3.3 원안 + 기준점 40)에는 서로 다른 세 가지 문제가 겹쳐 있었다.

**1. 비대칭 — 하락 근거를 점수로 표현할 수 없었다.**
가점이 +110, 감점이 -30 이었다. MACD 는 골든크로스에 +15 를 주면서 데드크로스는
계산조차 하지 않았고(`macd_golden_cross` 하나뿐이었다), MA 정배열에 +15 를 주면서
역배열에는 0점이었다. 그래서 중립 시장의 점수가 10 이 되었고, §3.2 의 60/40 임계값과
맞추려고 기준점 40 을 얹는 보정이 필요했다. v3 은 모든 항목을 0 을 중심으로 대칭으로
만들어 **기준점 50 이 곧 중립**이 되게 했다 — 자를 옮기는 대신 눈금을 고쳤다.

**2. 중복 — 같은 것을 세 번 셌다.**
RSI·스토캐스틱·CCI 는 실측 상관이 r = 0.76 ~ 0.84 로 사실상 같은 지표인데
(`tests/test_indicator_independence.py`), 합쳐서 40점을 차지했다. "여러 근거가
일치한다"는 착각이 점수로 굳는 구조였다. v3 은 이 축을 **RSI 하나 10점**으로 합쳤다.

**3. 검증되지 않은 배점.**
호가 불균형 10점은 백테스트 표본 9,443건 중 **0건에서만 켜졌다** — 백테스트에
호가 데이터가 없어 한 번도 검증된 적이 없는 점수였다. 운영에서만 조용히 10점을
움직이고 있었다. 참고 표시로 내리고 점수에서 뺐다.

### 왜 실측에 "맞춰" 쓰지 않았는가

같은 진단을 세 시기(최근 14일 / 28~42일 전 / 56~70일 전, 각 5,700여 표본)에서 돌렸더니
**개별 항목의 예측력 부호가 시기마다 뒤집혔다.** RSI 는 최근 구간에서 과매도가 가장
나빴지만(-0.029%) 56~70일 전 구간에서는 과매도가 가장 좋았다(+0.591%). 추세 계열도
최근 구간에서만 뚜렷했고 나머지 두 구간에서는 차이가 사라졌다.

그래서 v3 은 **어느 구간의 수치에도 맞추지 않았다.** 고친 것은 세 가지 모두 데이터의
방향이 아니라 구조다 — 비대칭, 중복, 검증 불가. 임계값(RSI 30/70, ADX 25, 거래량 30%)은
v2 그대로 두었다. 여기서 숫자를 데이터에 맞춰 돌리기 시작하면 그건 과최적화다.

상한 100 은 §3.3 의 `min(sum, 100)` 이고, 하한 0 은 우리 쪽 제약이다
(`ai_signals.tech_score` 가 TINYINT UNSIGNED 라 음수를 저장할 수 없다).

점수만 내지 않고 항목별 내역(`items`)을 함께 돌려준다. "왜 이 점수인가"를 UI 와
`data_sources_json` 에 그대로 실을 수 있어야 하기 때문이다 (README §4 "왜"를 보여준다).
점수에서 뺀 항목도 0점짜리 줄로 남긴다 — 화면에서 사라지면 안 되기 때문이다.
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

# --- 배점표 상수 (v3) --------------------------------------------------------
# 기준점. 모든 항목이 0 을 중심으로 대칭이므로 이 값이 곧 중립 점수다.
BASE_SCORE = 50.0

# 모멘텀. RSI·스토캐스틱·CCI 를 합친 하나의 축이다 (실측 상관 r = 0.76~0.84).
RSI_WEIGHT = 10.0
RSI_OVERSOLD = 30.0  # 이하이면 +RSI_WEIGHT
RSI_OVERBOUGHT = 70.0  # 이상이면 -RSI_WEIGHT
RSI_NEUTRAL = 50.0

MACD_CROSS_POINTS = 10.0  # 골든 +10 / 데드 -10
MA_TREND_POINTS = 15.0  # 정배열 +15 / 역배열 -15
ADX_POINTS = 10.0  # 배열 방향으로 ±10
ADX_TREND_MIN = 25.0
BOLLINGER_POINTS = 5.0  # 하단 복귀 +5 / 상단 돌파 지속 -5
VOLUME_SURGE_POINTS = 5.0  # 추세 방향으로 ±5
VOLUME_SURGE_MIN = 0.30  # 직전 봉 대비 +30%

# --- 점수에서 뺀 항목 (참고 표시로만 남긴다) ---------------------------------
# 스토캐스틱·CCI: RSI 와 중복 (r = 0.76~0.84).
# 호가 불균형:   백테스트에서 검증 불가 (표본 9,443건 중 0건).
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
CCI_OVERSOLD = -100.0
IMBALANCE_MIN = 0.15
REFERENCE_ONLY = "참고 지표 (점수 제외)"

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
    """RSI 를 ±10점으로. 30 이하 +10, 70 이상 -10, 50 이 정확히 0.

    **모멘텀 축 전체를 대표한다.** v2 에서는 RSI 20점 + 스토캐스틱 10점 + CCI 10점을
    따로 줬지만 셋의 실측 상관이 r = 0.76~0.84 라 같은 것을 세 번 센 셈이었다.

    값이 없으면 0점(중립)이다. 워밍업 중이라는 이유로 어느 쪽으로도 기울면 안 된다.
    """
    if rsi is None:
        return 0.0, "RSI 없음 → 중립"

    # 50 을 0 으로 두고 과매도 쪽을 양수로. 30/70 밖은 만점·만감점으로 자른다.
    span = RSI_NEUTRAL - RSI_OVERSOLD
    points = (RSI_NEUTRAL - rsi) / span * RSI_WEIGHT
    points = min(max(points, -RSI_WEIGHT), RSI_WEIGHT)

    return points, f"RSI {rsi:.1f} → {points:+.1f}점"


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
        # 내역의 합이 곧 점수여야 한다. 기준점도, 점수에서 뺀 항목도 한 줄로 남긴다.
        items: list[ScoreItem] = [ScoreItem("base", BASE_SCORE, "기준점 (중립 50점)")]

        points, detail = rsi_points(_number(snapshot.get("rsi")))
        items.append(ScoreItem("rsi", points, detail))

        trend = snapshot.get("ma_trend")
        items.append(self._ma_trend_item(trend))
        items.append(self._macd_item(snapshot))
        items.append(self._adx_item(snapshot, trend))
        items.append(self._bollinger_item(snapshot))
        items.append(self._volume_item(snapshot, trend))

        # 점수에서 뺀 항목 — 화면에는 계속 보여주되 합계는 움직이지 않는다.
        items.extend(self._reference_items(snapshot))

        raw_sum = sum(item.points for item in items)
        return TechScore(
            score=min(max(raw_sum, SCORE_MIN), SCORE_MAX),
            raw_sum=raw_sum,
            items=tuple(items),
        )

    # --- 점수 항목 -----------------------------------------------------------

    def _ma_trend_item(self, trend: Any) -> ScoreItem:
        """정배열 +15 / 역배열 -15. v2 는 역배열에 0점이라 하락을 표현하지 못했다."""
        if trend == TREND_BULLISH:
            return ScoreItem("ma_trend", MA_TREND_POINTS, "단기 정배열 (MA5>MA20>MA60)")
        if trend == TREND_BEARISH:
            return ScoreItem("ma_trend", -MA_TREND_POINTS, "단기 역배열 (MA5<MA20<MA60)")
        return ScoreItem("ma_trend", 0.0, f"MA 배열 {trend}")

    def _macd_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        """골든 +10 / 데드 -10. v2 는 데드크로스를 계산조차 하지 않았다."""
        if bool(snapshot.get("macd_golden_cross")):
            return ScoreItem("macd_cross", MACD_CROSS_POINTS, "골든크로스")
        if bool(snapshot.get("macd_dead_cross")):
            return ScoreItem("macd_cross", -MACD_CROSS_POINTS, "데드크로스")
        return ScoreItem("macd_cross", 0.0, "교차 없음")

    def _adx_item(self, snapshot: Mapping[str, Any], trend: Any) -> ScoreItem:
        """ADX 는 추세의 '세기'만 알려주고 방향은 모른다.

        그래서 MA 배열이 정한 방향에 세기를 곱한다. 배열이 혼조면 강화할 방향이
        없으므로 0점이다.
        """
        adx = _number(snapshot.get("adx"))
        if adx is None:
            return ScoreItem("adx", 0.0, "ADX 없음")
        if adx < ADX_TREND_MIN:
            return ScoreItem("adx", 0.0, f"추세 약함 ADX {adx:.1f}")
        if trend == TREND_BULLISH:
            return ScoreItem("adx", ADX_POINTS, f"상승 추세 강함 ADX {adx:.1f}")
        if trend == TREND_BEARISH:
            return ScoreItem("adx", -ADX_POINTS, f"하락 추세 강함 ADX {adx:.1f}")
        return ScoreItem("adx", 0.0, f"ADX {adx:.1f} 이나 방향 혼조")

    def _bollinger_item(self, snapshot: Mapping[str, Any]) -> ScoreItem:
        prev_close = _number(snapshot.get("prev_close"))
        close = _number(snapshot.get("close"))
        if bollinger_reentry(
            prev_close,
            _number(snapshot.get("prev_bb_lower")),
            close,
            _number(snapshot.get("bb_lower")),
        ):
            return ScoreItem("bollinger", BOLLINGER_POINTS, "하단 이탈 후 복귀")
        if bollinger_breakout_sustained(
            prev_close,
            _number(snapshot.get("prev_bb_upper")),
            close,
            _number(snapshot.get("bb_upper")),
        ):
            return ScoreItem("bollinger", -BOLLINGER_POINTS, "상단 돌파 지속")
        position = snapshot.get("bollinger_position") or "판정 불가"
        if position == BB_BELOW_LOWER:
            position = "하단 이탈 (복귀 대기)"
        elif position == BB_ABOVE_UPPER:
            position = "상단 돌파 (첫 봉)"
        return ScoreItem("bollinger", 0.0, str(position))

    def _volume_item(self, snapshot: Mapping[str, Any], trend: Any) -> ScoreItem:
        """거래량 급증은 방향을 모른다. 그래서 ADX 처럼 배열 방향에 실어 준다.

        v2 는 급증에 무조건 +10 을 줬다. 하락 추세에서 터진 거래량까지 상승 근거로
        세는 셈이었다.
        """
        rate = _number(snapshot.get("volume_change_rate"))
        if rate is None:
            return ScoreItem("volume", 0.0, "거래량 없음")
        if rate < VOLUME_SURGE_MIN:
            return ScoreItem("volume", 0.0, f"거래량 {rate * 100:+.0f}%")
        if trend == TREND_BULLISH:
            return ScoreItem("volume", VOLUME_SURGE_POINTS, f"상승 중 거래량 +{rate * 100:.0f}%")
        if trend == TREND_BEARISH:
            return ScoreItem(
                "volume", -VOLUME_SURGE_POINTS, f"하락 중 거래량 +{rate * 100:.0f}%"
            )
        return ScoreItem("volume", 0.0, f"거래량 +{rate * 100:.0f}% (방향 혼조)")

    # --- 참고 표시 (점수 0) ---------------------------------------------------

    def _reference_items(self, snapshot: Mapping[str, Any]) -> list[ScoreItem]:
        """점수에서 뺐지만 화면과 기록에는 남는 항목들.

        빼면서 지워버리면 "왜 뺐는지"도 같이 사라진다. 0점짜리 줄로 남겨 두면
        나중에 다시 넣을지 판단할 근거(값 자체)가 계속 쌓인다.
        """
        k = _number(snapshot.get("stochastic_k"))
        if k is None:
            stoch = "해당 없음"
        elif k >= STOCH_OVERBOUGHT:
            stoch = f"과매수 %K {k:.1f}"
        elif k <= STOCH_OVERSOLD:
            stoch = f"과매도 %K {k:.1f}"
        else:
            stoch = f"%K {k:.1f}"

        cci = _number(snapshot.get("cci"))
        cci_detail = "해당 없음" if cci is None else f"CCI {cci:.0f}"
        if cci is not None and cci <= CCI_OVERSOLD:
            cci_detail = f"과매도 CCI {cci:.0f}"

        imbalance = _number(snapshot.get("orderbook_imbalance"))
        if imbalance is None:
            book = "호가 없음"
        elif imbalance > IMBALANCE_MIN:
            book = f"매수 우위 {imbalance * 100:.0f}%"
        elif imbalance < -IMBALANCE_MIN:
            book = f"매도 우위 {imbalance * 100:.0f}%"
        else:
            book = f"불균형 {imbalance * 100:+.0f}%"

        divergence = _number(snapshot.get("vwap_divergence"))
        vwap = "VWAP 없음" if divergence is None else f"VWAP 대비 {divergence:+.2f}%"

        return [
            ScoreItem("stochastic", 0.0, f"{stoch} — RSI 와 중복 (r=0.84)"),
            ScoreItem("cci", 0.0, f"{cci_detail} — RSI 와 중복 (r=0.82)"),
            ScoreItem("orderbook", 0.0, f"{book} — 백테스트 검증 불가"),
            ScoreItem("vwap", 0.0, f"{vwap} — RSI 와 중복 (r=0.80)"),
        ]


def _number(value: Any) -> float | None:
    """bool 은 숫자가 아니다. NaN 도 값으로 치지 않는다."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number
