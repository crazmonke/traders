"""뉴스 감성 분류 (OpenAI).

제목 + 요약만 보낸다. 원문을 가져오지 않으므로 보낼 것도 없고, 헤드라인은
짧아 비용이 낮다. 한 번 분류한 기사는 `article_sentiments` 의
UNIQUE(article_id, classifier) 덕분에 다시 호출하지 않는다.

**분류 결과는 신호 점수에 들어가지 않는다.** 상관관계가 검증되기 전까지는
관찰용이다. (docs/EXTERNAL_DATA.md)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from trading_engine.external.news import store

log = logging.getLogger(__name__)

STANCES = ("BULLISH", "BEARISH", "NEUTRAL")
BATCH_SIZE = 25

SYSTEM_PROMPT = (
    "당신은 암호화폐 시장 뉴스를 분류한다. 각 기사 제목/요약이 암호화폐 가격에 "
    "미칠 방향성을 BULLISH(상승)/BEARISH(하락)/NEUTRAL(중립) 중 하나로 판정하라. "
    "기사가 이미 일어난 가격 변동을 사후 보도하는 것이라면 NEUTRAL 로 둔다. "
    "확신이 없으면 NEUTRAL 이다. confidence 는 0.0~1.0."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "stance": {"type": "string", "enum": list(STANCES)},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "stance", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _model() -> str | None:
    """뉴스는 헤드라인만 보면 되므로 신호 분석(OPENAI_MODEL)과 모델을 나눈다.

    OPENAI_NEWS_MODEL 이 없으면 OPENAI_MODEL 로 물러선다.
    """
    for name in ("OPENAI_NEWS_MODEL", "OPENAI_MODEL"):
        model = os.getenv(name, "").strip()
        # .env.example 의 자리표시자가 그대로 들어오는 경우를 막는다
        if model and not model.startswith("REPLACE_WITH"):
            return model
    return None


def classifier_name() -> str:
    """분류기 식별자에 모델명을 넣는다. 모델을 바꾸면 다른 분류기로 취급된다."""
    return f"openai:{_model() or 'unset'}"


async def classify_pending(limit: int = BATCH_SIZE) -> int:
    """미분류 기사를 한 배치 분류한다. 분류한 건수를 돌려준다.

    OpenAI 키나 모델이 설정돼 있지 않으면 조용히 0을 돌려준다 —
    수집은 분류 없이도 계속돼야 한다.
    """
    if not os.getenv("OPENAI_API_KEY"):
        log.debug("OPENAI_API_KEY 없음 - 분류를 건너뛴다 (수집은 계속된다)")
        return 0
    model = _model()
    if model is None:
        log.warning("OPENAI_MODEL 이 설정되지 않아 분류를 건너뛴다")
        return 0

    name = classifier_name()
    pending = await store.unclassified(name, limit)
    if not pending:
        return 0

    try:
        from openai import AsyncOpenAI
    except ImportError:
        log.warning("openai 패키지 없음 - 분류를 건너뛴다")
        return 0

    payload = [
        {
            "id": row["id"],
            "title": row["title"],
            "summary": (row["summary"] or "")[:300],
        }
        for row in pending
    ]

    try:
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "news_sentiments",
                    "schema": RESPONSE_SCHEMA,
                    "strict": True,
                },
            },
        )
        parsed: dict[str, Any] = json.loads(response.choices[0].message.content)
    except Exception:
        log.exception("뉴스 감성 분류 호출 실패 - 다음 주기에 다시 시도한다")
        return 0

    valid_ids = {row["id"] for row in pending}
    saved = 0
    for item in parsed.get("results", []):
        article_id = item.get("id")
        stance = item.get("stance")
        # 모델이 없는 id 나 엉뚱한 라벨을 지어내는 경우를 막는다
        if article_id not in valid_ids or stance not in STANCES:
            continue
        confidence = item.get("confidence")
        try:
            confidence = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
        await store.save_sentiment(article_id, name, stance, confidence)
        saved += 1

    log.info("뉴스 감성 분류: %d/%d건 저장 (%s)", saved, len(pending), name)
    return saved
