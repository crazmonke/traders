"""OpenAI Structured Output 호출과 응답 검증. (prompt.md v2 [Step 2] 요구사항 4)

호출 여부를 정하는 것은 이 모듈이 아니다. `strategy/rule_engine.should_request_ai` 가
Tech 70↑/30↓ + Consensus 50%↑ 로 걸러내고, 중복 호출은 Redis TTL 키가 막는다.
여기까지 온 요청만 실제로 돈을 쓴다.

**모델 응답을 그대로 믿지 않는다.** 스키마를 통과해도 값은 이상할 수 있어서
(`ai_score` 가 300, 확률 합이 250) 범위와 개수를 직접 검사한다. 검사에 실패하면
None 을 돌려주고 신호는 저장되지 않는다 — 근거 없는 점수를 DB 에 남기는 것보다 낫다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# `.env` 로딩(load_dotenv)을 위해 import 한다. 아래에서 OPENAI_* 를 os.getenv 로 읽는데,
# 이 모듈만 단독으로 import 되면 .env 가 안 읽혀 "미설정"으로 조용히 넘어간다.
from trading_engine import config  # noqa: F401
from trading_engine.ai.prompt import (
    RESPONSE_SCHEMA,
    SIGNAL_ENUM,
    SYSTEM_PROMPT,
    build_user_payload,
)

log = logging.getLogger(__name__)

# prompt.md §4 의 개수 제약. strict 스키마에 넣을 수 없어 여기서 검사한다.
MIN_REASONS, MAX_REASONS = 2, 5
MIN_RISKS, MAX_RISKS = 1, 3

# 확률 합계 허용 범위. 벗어나면 신호를 HOLD 로 강등한다 (ROADMAP Step 2 DoD).
# 정확히 100 을 요구하지 않는 것은 모델이 소수점을 반올림해 99.9 를 내는 일이 잦아서다.
PROBABILITY_SUM_MIN = 95.0
PROBABILITY_SUM_MAX = 105.0

MAX_REASON_LENGTH = 500


@dataclass(frozen=True)
class AiAnalysis:
    """검증을 통과한 AI 응답."""

    signal: str
    ai_score: float
    up_prob: float
    sideways_prob: float
    down_prob: float
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    model: str

    @property
    def probability_sum(self) -> float:
        return self.up_prob + self.sideways_prob + self.down_prob

    @property
    def is_probability_sum_valid(self) -> bool:
        return PROBABILITY_SUM_MIN <= self.probability_sum <= PROBABILITY_SUM_MAX

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "ai_score": self.ai_score,
            "probabilities": {
                "up": self.up_prob,
                "sideways": self.sideways_prob,
                "down": self.down_prob,
            },
            "reasons": list(self.reasons),
            "risks": list(self.risks),
            "model": self.model,
        }


def model_name() -> str | None:
    """신호 분석용 모델. 뉴스 분류(`OPENAI_NEWS_MODEL`)와 다른 모델을 쓸 수 있다."""
    model = os.getenv("OPENAI_MODEL", "").strip()
    # .env.example 의 자리표시자가 그대로 들어오는 경우를 막는다
    if not model or model.startswith("REPLACE_WITH"):
        return None
    return model


def is_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) and model_name() is not None


def _clean_texts(values: Any, low: int, high: int) -> tuple[str, ...] | None:
    """문자열 배열을 다듬는다. 개수가 넘치면 자르고, 모자라면 None.

    상한을 넘겼다고 응답 전체를 버리지 않는다 — 호출 비용은 이미 냈고, 근거가 많은 것은
    틀린 것이 아니다. 시스템 프롬프트에서 "most important first" 를 요구하므로 앞에서 자른다.
    하한 미만은 다르다. 근거 한 줄짜리 신호는 설명을 못 하므로 버린다.
    """
    if not isinstance(values, list):
        return None
    texts = tuple(
        item.strip()[:MAX_REASON_LENGTH]
        for item in values
        if isinstance(item, str) and item.strip()
    )
    if len(texts) < low:
        return None
    return texts[:high]


def normalize_probabilities(
    up: float, sideways: float, down: float
) -> tuple[float, float, float]:
    """0~1 로 온 확률을 퍼센트로 바꾼다.

    §4 스키마는 단위를 못 박지 않아 모델이 0.73/0.16/0.11 처럼 분수로 주는 일이 있다
    (gpt-5-mini 실측, 2026-09-03). 그대로 두면 합계 1.0 이라 95~105 검사에서 매번 HOLD 로
    강등되고 `up_prob` 에 0.73 이 저장된다.

    합계가 1 근처일 때만 바꾼다. 그 밖의 값은 손대지 않고 합계 검사에 맡긴다 —
    모델이 분포를 못 낸 것과 단위를 다르게 쓴 것은 다른 문제이고, 전자는 걸러야 한다.
    """
    total = up + sideways + down
    if PROBABILITY_SUM_MIN / 100.0 <= total <= PROBABILITY_SUM_MAX / 100.0:
        return up * 100.0, sideways * 100.0, down * 100.0
    return up, sideways, down


def parse(payload: Mapping[str, Any], model: str) -> AiAnalysis | None:
    """모델 응답(dict)을 검증해 `AiAnalysis` 로. 하나라도 어긋나면 None."""
    signal = payload.get("signal")
    if signal not in SIGNAL_ENUM:
        log.warning("AI 응답의 signal 이 스펙 밖이다: %r", signal)
        return None

    try:
        ai_score = float(payload["ai_score"])
        probabilities = payload["probabilities"]
        up = float(probabilities["up"])
        sideways = float(probabilities["sideways"])
        down = float(probabilities["down"])
    except (KeyError, TypeError, ValueError):
        log.warning("AI 응답에서 점수/확률을 읽지 못했다: %r", payload)
        return None

    if not 0.0 <= ai_score <= 100.0:
        log.warning("AI 응답의 ai_score 가 0~100 밖이다: %s", ai_score)
        return None
    if any(value < 0.0 for value in (up, sideways, down)):
        log.warning("AI 응답에 음수 확률이 있다: %s/%s/%s", up, sideways, down)
        return None
    up, sideways, down = normalize_probabilities(up, sideways, down)

    reasons = _clean_texts(payload.get("reasons"), MIN_REASONS, MAX_REASONS)
    risks = _clean_texts(payload.get("risks"), MIN_RISKS, MAX_RISKS)
    if reasons is None or risks is None:
        log.warning("AI 응답의 reasons/risks 개수가 §4 규격을 벗어났다")
        return None

    return AiAnalysis(
        signal=signal,
        ai_score=ai_score,
        up_prob=up,
        sideways_prob=sideways,
        down_prob=down,
        reasons=reasons,
        risks=risks,
        model=model,
    )


async def analyze(
    symbol: str,
    snapshot: Mapping[str, Any],
    consensus_pct: float,
    data_sources: Sequence[str],
) -> AiAnalysis | None:
    """한 심볼을 분석한다. 설정이 없거나 호출이 실패하면 None (신호는 저장되지 않는다)."""
    model = model_name()
    if model is None or not os.getenv("OPENAI_API_KEY"):
        log.warning("OPENAI_API_KEY/OPENAI_MODEL 미설정 - AI 분석을 건너뛴다 (%s)", symbol)
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        log.warning("openai 패키지 없음 - AI 분석을 건너뛴다")
        return None

    user_payload = build_user_payload(symbol, snapshot, consensus_pct, data_sources)
    try:
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(symbol=symbol)},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "trading_signal",
                    "schema": RESPONSE_SCHEMA,
                    "strict": True,
                },
            },
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception:
        # 다음 봉에서 다시 시도한다. 시세 수집을 멈추면 안 되므로 밖으로 던지지 않는다.
        log.exception("AI 분석 호출 실패 (%s)", symbol)
        return None

    return parse(parsed, model)
