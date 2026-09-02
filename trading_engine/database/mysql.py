"""MySQL 접근 계층.

엔진은 지금까지 Redis 만 썼다. 뉴스 아카이브부터 영구 저장이 필요해진다.

쓰기 빈도가 낮아(뉴스 폴링 5분 주기) 비동기 드라이버를 새로 들이지 않고
PyMySQL 을 `asyncio.to_thread` 로 감싼다. 이벤트 루프를 막지 않으면서
의존성은 그대로다. 고빈도 쓰기가 생기면 그때 aiomysql 을 검토한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

import pymysql

from trading_engine.config import settings

log = logging.getLogger(__name__)


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _run(sql: str, params: Sequence[Any] | None, fetch: str | None) -> Any:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.lastrowid


def _run_many(sql: str, rows: Sequence[Sequence[Any]]) -> int:
    if not rows:
        return 0
    with _connect() as conn, conn.cursor() as cur:
        return cur.executemany(sql, rows)


async def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """INSERT/UPDATE/DELETE. 반환값은 lastrowid."""
    return await asyncio.to_thread(_run, sql, params, None)


async def execute_many(sql: str, rows: Sequence[Sequence[Any]]) -> int:
    return await asyncio.to_thread(_run_many, sql, rows)


async def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    return await asyncio.to_thread(_run, sql, params, "one")


async def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_run, sql, params, "all")


async def ping() -> bool:
    row = await fetch_one("SELECT 1 AS ok")
    return bool(row and row.get("ok") == 1)
