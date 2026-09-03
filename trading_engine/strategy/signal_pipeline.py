"""신호 파이프라인 — 평가(Step 2-a) 위에 AI 분석·저장·publish 를 얹는다.

    SignalEngine.publish  → 합의·룰 점수 계산 + `consensus:{symbol}:{tf}` 캐싱
        ↓ needs_ai (Tech 70↑/30↓ + Consensus 50%↑ + 표본 충분)
    Redis SET NX          → 같은 봉에서 두 번 부르지 않는다
        ↓
    AiBudget.allow        → 지금 그 돈을 쓸 것인가 (off/seed/full)
        ↓
    ai.analyzer.analyze   → OpenAI Structured Output (§4)
        ↓ 확률 합계 95~105 검사
    strategy.store        → `ai_signals` INSERT
        ↓
    RedisStore.publish_signal → `channel:signals`

모든 단계가 실패해도 예외를 밖으로 던지지 않는다. 이 파이프라인은 시세 수집 태스크의
콜백 안에서 돌기 때문에, 여기서 던진 예외는 수집을 멈춘다.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Awaitable, Callable, Sequence

from trading_engine.ai import analyzer as ai_analyzer
from trading_engine.ai.analyzer import AiAnalysis
from trading_engine.market.redis_store import RedisStore
from trading_engine.strategy import store as signal_store
from trading_engine.strategy.ai_budget import AiBudget
from trading_engine.strategy.signal_engine import (
    SIGNAL_HOLD,
    SignalEngine,
    SignalEvaluation,
)

log = logging.getLogger(__name__)

Analyzer = Callable[
    [str, dict[str, Any], float, Sequence[str]], Awaitable[AiAnalysis | None]
]
"""(symbol, snapshot, consensus_pct, data_sources) → 분석 결과."""

Saver = Callable[[SignalEvaluation, AiAnalysis], Awaitable[int | None]]

# AI 호출 중복 차단 TTL(초). 5분봉 한 개 길이 — 봉마다 한 번까지만 부른다.
AI_CALL_TTL_SEC = 300


class SignalPipeline:
    """평가 결과를 실제 신호로 확정한다."""

    def __init__(
        self,
        engine: SignalEngine,
        store: RedisStore,
        analyzer: Analyzer | None = None,
        saver: Saver | None = None,
        ai_call_ttl_sec: int = AI_CALL_TTL_SEC,
        budget: AiBudget | None = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._analyze = analyzer or ai_analyzer.analyze
        self._save = saver or signal_store.save_signal
        self._ttl = ai_call_ttl_sec
        self._budget = budget or AiBudget(store)

    @property
    def budget(self) -> AiBudget:
        return self._budget

    async def run(self, base: str, force: bool = False) -> SignalEvaluation | None:
        """한 심볼을 평가하고, 조건이 맞으면 AI 분석까지 붙여 확정한다.

        돌려주는 값은 확정된 평가다. AI 를 부르지 않았으면 Step 2-a 평가 그대로다.
        """
        evaluation = await self._engine.publish(base, force=force)
        if evaluation is None:
            return None
        if not evaluation.needs_ai:
            return evaluation

        cached = await self._cached_analysis(evaluation)
        if cached is not None:
            # 봉 안에서는 AI 점수를 고정하고 나머지 80%만 갱신한다. 저장·publish 는 하지 않는다.
            return await self._confirm(evaluation, cached, record=False)

        if not self._budget.enabled:
            return evaluation

        if not await self._claim(evaluation):
            log.debug("%s AI 호출 건너뜀 - 이번 봉에서 이미 호출했다", base)
            return evaluation

        # 예산 확인은 봉 차단을 통과한 뒤에 한다. 순서를 바꾸면 호출이 실패했을 때
        # 다음 평가에서 seed 슬롯만 소비하고 봉 차단에 막혀 슬롯이 그냥 버려진다.
        if not await self._budget.allow(base):
            log.debug("%s AI 호출 보류 - 예산 모드 %s", base, self._budget.mode)
            return evaluation

        analysis = await self._analyze(
            evaluation.symbol,
            dict(evaluation.snapshot),
            evaluation.consensus.pct,
            evaluation.data_sources()["exchanges"],
        )
        if analysis is None:
            return evaluation

        await self._remember(evaluation, analysis)
        return await self._confirm(evaluation, analysis, record=True)

    async def _cached_analysis(self, evaluation: SignalEvaluation) -> AiAnalysis | None:
        """이번 봉에 받아둔 분석.

        AI 를 부르지 않았다고 직전 분석을 버리면, 5초마다 "AI 반영 신호 → 룰만 반영한
        신호" 로 등급이 오간다(실측: XRP BUY 79.7 → STRONG_BUY 84.0). AI 점수는 봉 단위로
        고정하고 나머지 80%(Tech·Consensus·Risk)만 갱신하는 것이 Final Score 의 정의에도 맞다.

        프로세스 메모리가 아니라 Redis 에 둔다. 차단 키는 재시작해도 남는데 분석만
        사라지면, 그 봉이 끝날 때까지 AI 를 못 부르면서 AI 없는 신호만 내보내게 된다.
        """
        try:
            raw = await self._store.load_ai_analysis(
                evaluation.symbol, evaluation.timeframe
            )
        except Exception:
            log.exception("AI 분석 캐시 조회 실패 (%s)", evaluation.symbol)
            return None
        if not raw:
            return None
        # 저장 형태가 모델 응답과 같은 스키마라 같은 검증기를 통과시킬 수 있다.
        return ai_analyzer.parse(raw, raw.get("model", "unknown"))

    async def _remember(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis
    ) -> None:
        try:
            await self._store.save_ai_analysis(
                evaluation.symbol, evaluation.timeframe, analysis.as_dict(), self._ttl
            )
        except Exception:
            log.exception("AI 분석 캐시 저장 실패 (%s)", evaluation.symbol)

    async def _confirm(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis, record: bool
    ) -> SignalEvaluation:
        """AI 를 반영한 평가를 확정한다. `record` 일 때만 DB 저장과 publish 를 한다."""
        confirmed = self._apply(evaluation, analysis)
        signal_id = await self._save(confirmed, analysis) if record else None
        # 룰만 반영한 평가가 캐시에 남지 않도록 덮어쓴다 (SignalEngine.publish 가 먼저 썼다).
        try:
            await self._store.save_consensus(
                confirmed.symbol, confirmed.as_dict(), confirmed.timeframe
            )
        except Exception:
            log.exception("합의 캐시 갱신 실패 (%s)", confirmed.symbol)
        if record:
            await self._publish(confirmed, analysis, signal_id)
        self._engine.latest[confirmed.symbol] = confirmed
        return confirmed

    def _apply(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis
    ) -> SignalEvaluation:
        """AI 점수를 Final Score 에 반영한다. 확률 합계가 이상하면 HOLD 로 강등한다."""
        if analysis.is_probability_sum_valid:
            return evaluation.with_ai(analysis.ai_score)

        # 확률 합이 95~105 를 벗어났다는 것은 모델이 분포를 제대로 못 낸 것이다.
        # 점수는 그대로 반영하되 등급만 HOLD 로 내린다 — 근거가 흔들리는 신호로
        # 매매까지 가게 두지 않는다. (ROADMAP Step 2 DoD)
        log.warning(
            "%s 확률 합계 %.1f 로 HOLD 강등 (up=%.1f sideways=%.1f down=%.1f)",
            evaluation.symbol,
            analysis.probability_sum,
            analysis.up_prob,
            analysis.sideways_prob,
            analysis.down_prob,
        )
        return dataclasses.replace(
            evaluation.with_ai(analysis.ai_score, signal_type=SIGNAL_HOLD),
            demoted_reason=f"AI 확률 합계 {analysis.probability_sum:.1f} (95~105 밖)",
        )

    async def _claim(self, evaluation: SignalEvaluation) -> bool:
        try:
            return await self._store.claim_ai_call(
                evaluation.symbol, evaluation.timeframe, self._ttl
            )
        except Exception:
            # Redis 가 흔들릴 때 호출을 막지 못하는 것보다, 아예 부르지 않는 쪽이 안전하다.
            log.exception("AI 호출 슬롯 선점 실패 - 이번 주기는 건너뛴다")
            return False

    async def _publish(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis, signal_id: int | None
    ) -> None:
        payload = evaluation.as_dict()
        payload["signal_id"] = signal_id
        payload["ai"] = analysis.as_dict()
        try:
            await self._store.publish_signal(payload)
        except Exception:
            log.exception("신호 publish 실패 (%s)", evaluation.symbol)
