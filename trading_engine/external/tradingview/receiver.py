"""유저별 트레이딩뷰 웹훅 수신. (prompt.md v2 [Step 3] 요구사항 2~7)

    POST /webhook/tv/{token}

**이것은 트레이딩뷰 시세를 우리가 가져오는 기능이 아니다.** 유저가 자기 계정에서 만든
Pine Script Alert 를 자기 대시보드로 중계받게 해주는 것뿐이다(prompt.md v2 §5).
그래서 여기 들어온 값을 "시세 데이터"로 취급하지 않고, 우리 신호 점수도 바꾸지 않는다.

엔진이 처음으로 **포트를 여는** 지점이다. 지금까지는 거래소·Redis·MySQL 로 나가기만 했다.
`WEBHOOK_ENABLED=0` 으로 끄면 서버를 띄우지 않으며, 꺼도 Step 1·2 수집·신호는 그대로 돈다.

### 404 를 돌려주는 방식

토큰이 없거나 폐기됐으면 404 다. 형식 오류(43자 base64url 이 아님)도 404 다.
"형식은 맞는데 없는 토큰"과 "형식부터 틀린 토큰"을 구분해 알려주면, 토큰을 긁어보는
쪽에 형식 정보를 주게 된다. 응답 본문도 같은 문구를 쓴다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response

from trading_engine.config import settings
from trading_engine.external.tradingview import store
from trading_engine.external.tradingview.ratelimit import RateLimiter
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

# PHP 쪽 `WebhookToken::LENGTH`/형식과 같아야 한다 (random_bytes(32) → base64url 43자).
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

# 트레이딩뷰가 보낼 수 있는 본문 크기 상한. Pine Script 알림 메시지는 작다.
MAX_BODY_BYTES = 16 * 1024

MAX_SYMBOL_LENGTH = 20  # external_signals.symbol VARCHAR(20)
MAX_STRATEGY_LENGTH = 100  # external_signals.strategy_name VARCHAR(100)

NOT_FOUND_MESSAGE = "찾을 수 없습니다."


class WebhookReceiver:
    """수신 라우터. Redis/저장소를 주입받아 테스트에서 갈아끼울 수 있다."""

    def __init__(
        self,
        store_module: Any = store,
        limiter: RateLimiter | None = None,
        link_window_min: int | None = None,
    ) -> None:
        self._store = store_module
        self._limiter = limiter
        self._link_window = (
            settings.webhook_link_window_min if link_window_min is None else link_window_min
        )

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            "/webhook/tv/{token}", self.receive, methods=["POST"], include_in_schema=False
        )
        return router

    async def receive(self, token: str, request: Request) -> Response:
        if not TOKEN_PATTERN.match(token):
            # DB 를 조회하기 전에 거른다. 형식만 봐도 우리가 발급한 토큰이 아니다.
            return self._not_found()

        if self._limiter is not None and not await self._limiter.allow(token):
            return _json({"error": "요청이 너무 잦습니다."}, 429)

        webhook = await self._store.find_active_webhook(token)
        if webhook is None:
            return self._not_found()

        payload = await self._read_json(request)
        if payload is None:
            return _json({"error": "JSON 본문이 필요합니다."}, 400)

        fields = self._extract(payload)
        if fields is None:
            return _json({"error": "symbol 과 action(BUY/SELL/EXIT) 이 필요합니다."}, 400)
        symbol, action, strategy_name = fields

        # 참고 연결만 한다. 우리 신호의 점수·등급은 건드리지 않는다 (요구사항 6).
        linked_signal_id = await self._store.find_linked_signal(symbol, self._link_window)

        signal_id = await self._store.save_signal(
            user_id=int(webhook["user_id"]),
            user_webhook_id=int(webhook["id"]),
            symbol=symbol,
            action=action,
            strategy_name=strategy_name,
            raw_payload=payload,
            linked_signal_id=linked_signal_id,
        )
        await self._store.touch_webhook(int(webhook["id"]))

        log.info(
            "웹훅 수신 user=%s webhook=%s %s %s (linked=%s)",
            webhook["user_id"],
            webhook["id"],
            symbol,
            action,
            linked_signal_id,
        )

        return _json(
            {"received": True, "id": signal_id, "linked_signal_id": linked_signal_id}, 201
        )

    def _not_found(self) -> Response:
        return _json({"error": NOT_FOUND_MESSAGE}, 404)

    async def _read_json(self, request: Request) -> dict[str, Any] | None:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES or not body.strip():
            return None
        try:
            decoded = json.loads(body)
        except ValueError:
            return None

        return decoded if isinstance(decoded, dict) else None

    def _extract(self, payload: dict[str, Any]) -> tuple[str, str, str | None] | None:
        """필수는 `symbol` 과 `action` 뿐이다 (요구사항 4).

        나머지는 유저마다 Pine Script 가 달라 강제하지 않는다. 원본은 통째로 저장하므로
        여기서 못 읽은 필드도 잃지 않는다.
        """
        symbol = payload.get("symbol")
        action = payload.get("action")
        if not isinstance(symbol, str) or not isinstance(action, str):
            return None

        symbol = symbol.strip().upper()[:MAX_SYMBOL_LENGTH]
        action = action.strip().upper()
        if not symbol or action not in store.ACTIONS:
            return None

        strategy = payload.get("strategy_name")
        strategy_name = (
            strategy.strip()[:MAX_STRATEGY_LENGTH]
            if isinstance(strategy, str) and strategy.strip()
            else None
        )

        return symbol, action, strategy_name


def _json(body: dict[str, Any], status: int) -> Response:
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        status_code=status,
        media_type="application/json",
    )


def create_app(redis_store: RedisStore | None = None) -> FastAPI:
    """수신 전용 앱. 다른 엔드포인트를 여기에 붙이지 않는다 — 공개 주소이기 때문이다."""
    app = FastAPI(
        title="Webhook Receiver",
        docs_url=None,  # 공개 주소에 스키마를 노출하지 않는다
        redoc_url=None,
        openapi_url=None,
    )
    limiter = (
        RateLimiter(redis_store, settings.webhook_rate_per_min)
        if redis_store is not None
        else None
    )
    app.include_router(WebhookReceiver(limiter=limiter).router())

    return app


async def serve(app: FastAPI) -> None:
    """수집 태스크와 같은 이벤트 루프에서 uvicorn 을 띄운다.

    `uvicorn.run()` 은 자기 루프를 새로 만들기 때문에 쓸 수 없다. Server.serve() 를
    코루틴으로 돌려야 거래소 수집 태스크와 한 프로세스에서 공존한다.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=settings.webhook_host,
        port=settings.webhook_port,
        log_level=settings.log_level.lower(),
        access_log=False,  # 수신 성공/실패는 우리가 직접 로그로 남긴다
    )
    await uvicorn.Server(config).serve()
