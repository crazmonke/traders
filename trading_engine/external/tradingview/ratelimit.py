"""토큰별 수신 요청 제한. (prompt.md v2 [Step 3] 요구사항 7)

웹훅 URL 은 로그인 없이 누구나 POST 할 수 있는 주소다. 토큰이 새어나가면 그 URL 로
무한정 요청이 들어올 수 있고, 요청 하나마다 DB 쓰기가 일어난다. 분 단위 고정 창으로
막는다.

고정 창(fixed window)이라 창 경계에서 최대 2배까지 통과할 수 있다. 트레이딩뷰 알림은
분당 수 건 수준이라 이 오차가 문제되지 않고, 슬라이딩 윈도우로 정밀하게 막을 만한
가치가 없다. 정확도가 필요해지면 그때 바꾼다.
"""

from __future__ import annotations

import logging
import time

from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

WINDOW_SEC = 60


def window_key(token: str, now: float | None = None) -> str:
    """분 단위로 갈리는 키. 창이 바뀌면 키가 바뀌므로 별도 초기화가 필요 없다."""
    minute = int((now if now is not None else time.time()) // WINDOW_SEC)
    return f"webhook:rate:{token}:{minute}"


class RateLimiter:
    def __init__(self, store: RedisStore, limit_per_min: int) -> None:
        self._store = store
        self._limit = limit_per_min

    async def allow(self, token: str) -> bool:
        """이번 요청을 받아도 되는지. 한도를 넘으면 False."""
        if self._limit <= 0:
            return True  # 0 이하는 "제한 없음"으로 읽는다

        key = window_key(token)
        try:
            count = await self._store.incr_with_expire(key, WINDOW_SEC)
        except Exception:
            # Redis 가 흔들릴 때 수신을 통째로 막으면 유저 알림이 유실된다.
            # 유실보다 초과 허용이 낫다 — 뒤에 DB 쓰기 말고는 비싼 작업이 없다.
            log.exception("수신 제한 확인 실패 - 이번 요청은 통과시킨다")
            return True

        return count <= self._limit
