"""신호 산출 파이프라인 — 거래소별 점수 → 합의 → 글로벌 점수 → Final Score.

Step 2-a 범위는 **결정론적인 부분 전부**다. OpenAI 호출과 `ai_signals` 저장/publish 는
Step 2-b 에서 이 모듈의 `SignalEvaluation` 을 입력으로 받아 붙인다. 그래서 여기서는
`ai_score` 를 인자로만 열어두고 아무도 채우지 않는다.

Final Score (prompt.md v2 §3.3):

    Final = S_Tech×0.45 + S_Consensus×0.20 + S_AI×0.20 + S_Risk×0.15

### 방향 정합에 대한 결정 (스펙 공백)

가중식을 글자 그대로 쓰면 하락 신호에서 뒤집힌 결과가 나온다. 예를 들어 5개 거래소가
모두 강한 하락(S_Tech 10)에 합의(S_Consensus 100)하고 변동성도 안정(S_Risk 100)이면
`10×0.45 + 100×0.2 + 100×0.15 = 39.5` 로, "확신에 찬 매도"가 어정쩡한 중간 점수를
받는다. 합의율과 위험 점수는 **방향이 아니라 신뢰도**를 재는 값인데 매수 쪽으로만
가산되기 때문이다.

그래서 Final Score 를 "정해진 방향(§3.2 의 다수 진영)을 얼마나 강하게 지지하는가"로
정의하고, 방향성을 가진 항목(S_Tech, S_AI)만 SELL 진영에서 `100 - x` 로 뒤집는다.
가중치 구성비(45/20/20/15)는 스펙 그대로다. 이렇게 하면 매수·매도가 대칭이 되고,
`ai_signals.final_score` 인덱스(`idx_score`)와 Step 6 의 `GET /signals/strong` 이
매도 신호에도 그대로 쓸 수 있다.

### AI 는 점수에 들어가지 않는다 (2026-09-03 결정)

**LLM 은 가격을 예측하지 못한다.** 지표 숫자를 넣고 "상승 확률 81%" 를 물으면 그럴듯한
숫자를 만들어낼 뿐이고 그 숫자에는 근거가 없다. 검증할 수 없는 값을 점수의 20% 로 쓰면
전체 점수가 검증 불가능해진다.

그래서 **AI 는 "왜 이 신호가 나왔는지 사람 말로 설명하는" 역할만** 한다. 점수는 규칙과
통계로만 낸다 — 그래야 백테스트로 검증할 수 있고, 검증할 수 있어야 개선할 수 있다.

가중치는 §3.3 의 구성비에서 AI 몫(20%)을 빼고 **남은 셋을 비례 재분배**한다.
(원래도 AI 미호출 시 같은 재분배가 일어났으므로 계산 경로는 하나로 합쳐졌다.)

    Tech 45 → 56.25%   Consensus 20 → 25%   Risk 15 → 18.75%
"""

from __future__ import annotations

import logging
import time
import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping

from trading_engine.market.exchange_feed import CANDLE_TIMEFRAME
from trading_engine.market.market_manager import MarketManager
from trading_engine.market.redis_store import RedisStore
from trading_engine.strategy import consensus as consensus_mod
from trading_engine.strategy import risk as risk_mod
from trading_engine.strategy.consensus import BUY, NEUTRAL, SELL, ConsensusResult
from trading_engine.strategy.rule_engine import RuleEngine, TechScore, should_request_ai
from trading_engine.strategy.risk import RiskScore

log = logging.getLogger(__name__)

# §3.3 의 구성비에서 AI 를 뺀 것. 숫자는 스펙 그대로 두고 합이 0.8 이 되게 한 뒤
# 아래에서 비례 재분배한다 — 스펙과의 대응 관계를 눈으로 확인할 수 있어야 한다.
FINAL_WEIGHTS: dict[str, float] = {
    "tech": 0.45,
    "consensus": 0.20,
    "risk": 0.15,
}

# Final Score → 등급. prompt.md 에 임계값이 없어 이 프로젝트에서 정한다.
# 방향(§3.2 다수 진영)이 등급의 부호를, 아래 임계값이 세기를 정한다.
STRONG_THRESHOLD = 80.0
SIGNAL_THRESHOLD = 60.0

SIGNAL_STRONG_BUY = "STRONG_BUY"
SIGNAL_BUY = "BUY"
SIGNAL_HOLD = "HOLD"
SIGNAL_SELL = "SELL"
SIGNAL_STRONG_SELL = "STRONG_SELL"

# 한 심볼을 이 간격보다 자주 재평가하지 않는다. 지표는 거래소×심볼마다 갱신되므로
# 그대로 두면 심볼 하나가 초당 여러 번 평가된다.
EVAL_MIN_INTERVAL_SEC = 5.0


def align(value: float, direction: str) -> float:
    """방향성 점수를 "정해진 방향을 지지하는 정도"로 바꾼다. SELL 이면 뒤집는다."""
    return 100.0 - value if direction == SELL else value


def final_score(tech: float, consensus_pct: float, risk: float, direction: str) -> float:
    """§3.3 가중식에서 AI 를 뺀 것. 남은 셋을 비례 재분배한다.

    **AI 점수를 받지 않는다.** 받을 수 있게 열어두면 언젠가 누군가 넣는다.
    """
    parts = {
        "tech": align(tech, direction),
        "consensus": consensus_pct,
        "risk": risk,
    }
    total_weight = sum(FINAL_WEIGHTS.values())
    score = sum(value * FINAL_WEIGHTS[key] for key, value in parts.items()) / total_weight

    return min(max(score, 0.0), 100.0)


def classify_signal(direction: str, score: float, sample_sufficient: bool) -> str:
    """등급 판정. 표본이 3개 미만이면 무조건 HOLD (§3.2 4항)."""
    if not sample_sufficient or direction == NEUTRAL:
        return SIGNAL_HOLD
    if score >= STRONG_THRESHOLD:
        return SIGNAL_STRONG_BUY if direction == BUY else SIGNAL_STRONG_SELL
    if score >= SIGNAL_THRESHOLD:
        return SIGNAL_BUY if direction == BUY else SIGNAL_SELL
    return SIGNAL_HOLD


@dataclass(frozen=True)
class SignalEvaluation:
    """한 심볼의 결정론적 평가 결과. Step 2-b 가 여기에 AI 점수를 붙인다."""

    symbol: str
    timeframe: str
    signal_type: str
    tech: TechScore
    consensus: ConsensusResult
    risk: RiskScore
    final_score: float
    ai_score: float | None
    needs_ai: bool
    demoted_reason: str | None
    snapshot: Mapping[str, Any]
    evaluated_at: int

    @property
    def direction(self) -> str:
        return self.consensus.direction

    def with_ai(self, ai_score: float, signal_type: str | None = None) -> "SignalEvaluation":
        """AI 가 낸 점수를 **기록만** 한다. 등급과 Final Score 는 바뀌지 않는다.

        AI 는 설명 담당이라 점수에 들어가지 않는다(모듈 주석 참고). 그래도 `ai_score` 를
        남기는 이유는, 나중에 "룰과 AI 중 무엇이 더 맞았나"를 데이터로 비교하기 위해서다.

        `signal_type` 을 넘기면 그것으로 덮어쓴다 — AI 응답이 망가졌을 때 쓰는 통로다.
        """
        return dataclasses.replace(
            self,
            ai_score=ai_score,
            signal_type=signal_type or self.signal_type,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal_type": self.signal_type,
            "final_score": round(self.final_score, 2),
            "tech_score": round(self.tech.score, 2),
            "ai_score": None if self.ai_score is None else round(self.ai_score, 2),
            "risk_score": round(self.risk.score, 2),
            "exchange_consensus_pct": round(self.consensus.pct, 2),
            "direction": self.direction,
            "needs_ai": self.needs_ai,
            "demoted_reason": self.demoted_reason,
            "consensus": self.consensus.as_dict(),
            "tech": self.tech.as_dict(),
            "risk": self.risk.as_dict(),
            "entry_price_global": self.snapshot.get("price"),
            "entry_price_upbit": self.snapshot.get("upbit_price"),
            "evaluated_at": self.evaluated_at,
        }

    def data_sources(self) -> dict[str, Any]:
        """`ai_signals.data_sources_json` 에 넣을 형태 — 거래소 목록 + 거래소별 점수."""
        return {
            "exchanges": list(self.consensus.valid_exchanges),
            "global_price_sources": list(self.snapshot.get("sources") or []),
            "per_exchange_tech_score": {
                code: round(score, 2)
                for code, score in self.consensus.per_exchange.items()
            },
            "directions": self.consensus.counts,
        }


class SignalEngine:
    """지표가 갱신될 때마다 심볼 단위로 합의·점수를 낸다."""

    def __init__(
        self,
        manager: MarketManager,
        store: RedisStore,
        rule_engine: RuleEngine | None = None,
        timeframe: str = CANDLE_TIMEFRAME,
        min_interval_sec: float = EVAL_MIN_INTERVAL_SEC,
    ) -> None:
        self._manager = manager
        self._store = store
        self._rules = rule_engine or RuleEngine()
        self._timeframe = timeframe
        self._min_interval = min_interval_sec
        self._last_eval: dict[str, float] = {}  # 값이 없으면 "아직 평가한 적 없음"
        self.latest: dict[str, SignalEvaluation] = {}

    def exchange_scores(self, base: str) -> dict[str, float | None]:
        """거래소별 Technical Score. 캔들이 모자라 판단할 수 없으면 None.

        지표가 하나도 없는 스냅샷(연결 직후)을 0점으로 세면 전 거래소가 SELL 진영으로
        몰린다. 그런 거래소는 합의의 분모에서 빼야 한다 (consensus 모듈 주석 참고).
        """
        scores: dict[str, float | None] = {}
        for exchange, indicators in self._manager.indicators_for(base).items():
            if indicators.rsi is None:
                scores[exchange] = None
                continue
            scores[exchange] = self._rules.score(indicators.as_dict()).score
        return scores

    def evaluate(self, base: str) -> SignalEvaluation | None:
        """한 심볼을 평가한다. 부수효과 없음 — 테스트가 이 함수만 보면 된다."""
        snapshot = self._manager.aggregate(base)
        if snapshot is None:
            return None

        result = consensus_mod.compute(base, self.exchange_scores(base))
        tech = self._rules.score(snapshot)
        risk = risk_mod.compute(
            snapshot.get("atr"), snapshot.get("close"), result.valid_count
        )
        score = final_score(tech.score, result.pct, risk.score, result.direction)
        signal_type = classify_signal(
            result.direction, score, result.is_sample_sufficient
        )

        demoted_reason = None
        if not result.is_sample_sufficient:
            demoted_reason = (
                f"유효 거래소 {result.valid_count}개 "
                f"(<{consensus_mod.MIN_VALID_EXCHANGES}) — 표본 부족"
            )

        evaluation = SignalEvaluation(
            symbol=base,
            timeframe=self._timeframe,
            signal_type=signal_type,
            tech=tech,
            consensus=result,
            risk=risk,
            final_score=score,
            ai_score=None,
            needs_ai=should_request_ai(
                tech.score, result.pct, result.is_sample_sufficient
            ),
            demoted_reason=demoted_reason,
            snapshot=snapshot,
            evaluated_at=int(time.time() * 1000),
        )
        self.latest[base] = evaluation
        return evaluation

    async def publish(self, base: str, force: bool = False) -> SignalEvaluation | None:
        """평가하고 `consensus:{symbol}:{tf}` 에 캐싱한다. (prompt.md v2 §3.1)"""
        if not force and not self._should_evaluate(base):
            return None
        evaluation = self.evaluate(base)
        if evaluation is None:
            return None
        try:
            await self._store.save_consensus(
                base, evaluation.as_dict(), self._timeframe
            )
        except Exception:
            log.exception("합의 캐싱 실패 (symbol=%s)", base)
        return evaluation

    def _should_evaluate(self, base: str) -> bool:
        now = time.monotonic()
        last = self._last_eval.get(base)
        # 기본값 0.0 을 쓰면 안 된다. monotonic() 의 기준점은 플랫폼마다 다르고,
        # 부팅 직후 컨테이너에서는 now 가 min_interval 보다 작아 첫 평가를 건너뛴다.
        if last is None or now - last >= self._min_interval:
            self._last_eval[base] = now
            return True
        return False
