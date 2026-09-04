"""신호 파이프라인 — 평가(Step 2-a) 위에 AI 설명·기록·publish 를 얹는다.

**AI 경로와 기록 경로는 서로 독립이다** (2026-09-04 분리).

    SignalEngine.publish  → 합의·룰 점수 계산 + `consensus:{symbol}:{tf}` 캐싱
        │
        ├─ [AI 경로 — 설명 전용, 돈이 든다]
        │     needs_ai (Tech 70↑/30↓ + Consensus 50%↑ + 표본 충분)
        │       ↓ Redis SET NX — 같은 봉에서 두 번 부르지 않는다
        │       ↓ AiBudget.allow — off/seed/full
        │       ↓ ai.analyzer.analyze — OpenAI Structured Output (§4)
        │
        └─ [기록 경로 — 적중률 데이터, 공짜다]
              is_recordable (게이트 통과 + HOLD 아님)
                ↓ Redis SET NX — 봉당 한 번만
                ↓ strategy.store → `ai_signals` INSERT (AI 없으면 확률·설명은 NULL)
                ↓ RedisStore.publish_signal → `channel:signals`

### 왜 나눴는가

원래는 **AI 분석이 붙은 신호만** 저장했다. `up_prob` 등이 NOT NULL 이었기 때문이다.
그런데 AI 가 2026-09-03 부터 점수에서 빠졌는데도(설명 전용) AI 예산(seed = 심볼당
하루 5건)이 저장까지 막고 있었다. **점수에 영향도 없는 비용 통제가 이 서비스의 유일한
실적 데이터를 하루 25건으로 묶고 있었다** — 실측 21시간에 24건.

분리하면 게이트를 통과한 신호가 전부 기록된다(실측 게이트 통과율 약 20%, 하루 300건
안팎). **AI 비용은 그대로다.**

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
from trading_engine.strategy.signal_engine import SignalEngine, SignalEvaluation

log = logging.getLogger(__name__)

Analyzer = Callable[
    [str, dict[str, Any], float, Sequence[str]], Awaitable[AiAnalysis | None]
]
"""(symbol, snapshot, consensus_pct, data_sources) → 분석 결과."""

Saver = Callable[[SignalEvaluation, AiAnalysis | None], Awaitable[int | None]]

# AI 호출·기록 중복 차단 TTL(초). 5분봉 한 개 길이 — 봉마다 한 번까지만.
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
        """한 심볼을 평가하고, AI 설명을 붙일 수 있으면 붙인 뒤 확정한다.

        **AI 를 못 붙여도 기록은 한다.** 둘은 서로 독립이다(위 그림 참고).
        돌려주는 값은 확정된 평가다.
        """
        evaluation = await self._engine.publish(base, force=force)
        if evaluation is None:
            return None

        analysis = await self._explain(evaluation, base)

        return await self._settle(evaluation, analysis)

    async def _explain(
        self, evaluation: SignalEvaluation, base: str
    ) -> AiAnalysis | None:
        """AI 설명을 구해 온다. 실패·예산 초과·게이트 미통과면 None — **기록은 계속된다.**"""
        if not evaluation.needs_ai:
            return None

        cached = await self._cached_analysis(evaluation)
        if cached is not None:
            # 봉 안에서는 AI 점수를 고정한다. 5초마다 "AI 반영 → 룰만 반영"으로
            # 등급이 오가는 것을 막는다(`_cached_analysis` 주석 참고).
            return cached

        if not self._budget.enabled:
            return None

        if not await self._claim(evaluation):
            log.debug("%s AI 호출 건너뜀 - 이번 봉에서 이미 호출했다", base)
            return None

        # 예산 확인은 봉 차단을 통과한 뒤에 한다. 순서를 바꾸면 호출이 실패했을 때
        # 다음 평가에서 seed 슬롯만 소비하고 봉 차단에 막혀 슬롯이 그냥 버려진다.
        if not await self._budget.allow(base):
            log.debug("%s AI 호출 보류 - 예산 모드 %s", base, self._budget.mode)
            return None

        analysis = await self._analyze(
            evaluation.symbol,
            dict(evaluation.snapshot),
            evaluation.consensus.pct,
            evaluation.data_sources()["exchanges"],
        )
        if analysis is None:
            return None

        await self._remember(evaluation, analysis)
        return analysis

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

    async def _settle(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis | None
    ) -> SignalEvaluation:
        """평가를 확정한다. 기록 대상이면 봉당 한 번 DB 에 넣고 publish 한다."""
        confirmed = self._apply(evaluation, analysis) if analysis else evaluation

        record = await self._claim_record(confirmed)
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

    async def _claim_record(self, evaluation: SignalEvaluation) -> bool:
        """이 평가를 DB 에 남길 것인가. 봉당 한 번만 True.

        기록 대상이 아니면 Redis 를 건드리지 않는다 — 슬롯을 먼저 잡아버리면 그 봉에
        나중에 나온 진짜 신호를 놓친다(HOLD 였다가 방향이 잡히는 경우).
        """
        if not evaluation.is_recordable:
            return False
        try:
            return await self._store.claim_signal_record(
                evaluation.symbol, evaluation.timeframe, self._ttl
            )
        except Exception:
            # Redis 가 흔들릴 때 같은 봉을 여러 번 저장하는 것보다 한 봉을 놓치는 쪽이 낫다.
            log.exception("기록 슬롯 선점 실패 - 이번 주기는 건너뛴다 (%s)", evaluation.symbol)
            return False

    def _apply(
        self, evaluation: SignalEvaluation, analysis: AiAnalysis
    ) -> SignalEvaluation:
        """AI 결과를 붙인다. **등급은 바뀌지 않는다** — 등급은 룰이 정한다.

        확률 합계가 95~105 를 벗어나면 모델이 분포를 제대로 못 낸 것이다.
        **AI 가 점수에서 빠진 뒤로는 그것을 이유로 신호를 강등하지 않는다**
        (2026-09-03 결정). 룰이 낸 신호를 LLM 이 JSON 을 잘못 만들었다는 이유로
        약화시키는 것은 앞뒤가 안 맞는다. 대신 **그 확률을 유저에게 보여주지 않는다** —
        신뢰할 수 없는 숫자를 화면에 띄우는 것이 더 나쁘다.
        """
        confirmed = evaluation.with_ai(analysis.ai_score)
        if analysis.is_probability_sum_valid:
            return confirmed

        log.warning(
            "%s AI 확률 합계 %.1f (95~105 밖) - 확률은 신뢰 불가로 표시한다 (up=%.1f sideways=%.1f down=%.1f)",
            evaluation.symbol,
            analysis.probability_sum,
            analysis.up_prob,
            analysis.sideways_prob,
            analysis.down_prob,
        )
        return dataclasses.replace(
            confirmed,
            demoted_reason=f"AI 확률 합계 {analysis.probability_sum:.1f} (95~105 밖) - 확률 표시 안 함",
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
        self, evaluation: SignalEvaluation, analysis: AiAnalysis | None, signal_id: int | None
    ) -> None:
        payload = evaluation.as_dict()
        payload["signal_id"] = signal_id
        # AI 를 부르지 않았으면 None 이다. 구독자가 "설명 없음"을 알 수 있어야 한다.
        payload["ai"] = analysis.as_dict() if analysis else None
        try:
            await self._store.publish_signal(payload)
        except Exception:
            log.exception("신호 publish 실패 (%s)", evaluation.symbol)
