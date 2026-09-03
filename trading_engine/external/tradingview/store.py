"""웹훅 수신 영속화 — `user_webhooks` 조회와 `external_signals` 저장.

**모든 조회에 토큰 또는 user_id 가 조건으로 들어간다.** 이 데이터는 유저가 직접 만든
Pine Script 전략 정보라 유저 간에 절대 섞이면 안 된다(prompt.md v2 §5).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading_engine.database import mysql

log = logging.getLogger(__name__)

ACTIONS = ("BUY", "SELL", "EXIT")


async def find_active_webhook(token: str) -> dict[str, Any] | None:
    """살아 있는 웹훅만 돌려준다. 폐기됐거나 없으면 None.

    `is_active` 와 `revoked_at` 을 둘 다 본다 — 재발급(rotate)은 두 값을 함께 바꾸지만,
    운영 중 수동으로 한쪽만 건드리는 일이 생겨도 막히는 쪽으로 떨어져야 한다.
    """
    return await mysql.fetch_one(
        "SELECT id, user_id, webhook_token FROM user_webhooks "
        "WHERE webhook_token = %s AND is_active = 1 AND revoked_at IS NULL",
        (token,),
    )


async def find_linked_signal(symbol: str, window_minutes: int) -> int | None:
    """같은 심볼의 최근 `ai_signals` id. 없으면 None.

    **참고 연결일 뿐이다.** 이 신호의 점수·등급을 건드리지 않는다(prompt.md v2 [Step 3]
    요구사항 6). 유저가 만든 알림 하나가 맞았다고 우리 신호를 소급 수정하면, 그 신호로
    쌓은 적중률 통계가 무의미해진다.
    """
    row = await mysql.fetch_one(
        "SELECT id FROM ai_signals "
        "WHERE symbol = %s AND created_at >= NOW() - INTERVAL %s MINUTE "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (symbol, window_minutes),
    )
    return int(row["id"]) if row else None


async def save_signal(
    *,
    user_id: int,
    user_webhook_id: int,
    symbol: str,
    action: str,
    strategy_name: str | None,
    raw_payload: dict[str, Any],
    linked_signal_id: int | None,
) -> int:
    """`external_signals` 에 한 행. 원본 payload 를 그대로 남긴다.

    유저마다 Pine Script 가 달라 어떤 필드가 올지 모른다. 우리가 아는 필드만 컬럼에
    넣고 원본은 통째로 보관해, 나중에 필요해지면 거기서 꺼낸다.
    """
    return await mysql.execute(
        "INSERT INTO external_signals "
        "(user_id, user_webhook_id, symbol, action, strategy_name, "
        " raw_payload_json, linked_signal_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            user_id,
            user_webhook_id,
            symbol,
            action,
            strategy_name,
            json.dumps(raw_payload, ensure_ascii=False),
            linked_signal_id,
        ),
    )


async def touch_webhook(webhook_id: int) -> None:
    """`last_received_at` 갱신. 유저가 "연동이 살아 있나"를 확인하는 유일한 단서다."""
    await mysql.execute(
        "UPDATE user_webhooks SET last_received_at = CURRENT_TIMESTAMP WHERE id = %s",
        (webhook_id,),
    )


async def list_for_user(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """한 유저의 수신 이력. 대시보드(Step 9)와 격리 테스트가 쓴다.

    `user_id` 조건이 빠지면 유저 A 의 전략이 유저 B 화면에 뜬다.
    """
    return await mysql.fetch_all(
        "SELECT id, user_webhook_id, symbol, action, strategy_name, "
        "       linked_signal_id, received_at "
        "  FROM external_signals WHERE user_id = %s "
        " ORDER BY received_at DESC, id DESC LIMIT %s",
        (user_id, limit),
    )
