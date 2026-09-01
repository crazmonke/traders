"""환경 변수 로딩. 저장소 루트의 .env 를 읽는다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# 수집 대상 마켓 (prompt.md [Step 1] 요구사항 2)
DEFAULT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]


def _split(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    redis_host: str = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str | None = os.getenv("REDIS_PASSWORD") or None
    markets: list[str] = field(
        default_factory=lambda: _split(os.getenv("MARKETS"), DEFAULT_MARKETS)
    )
    # Redis 캐시 TTL(초). 엔진이 죽었을 때 낡은 시세가 살아있는 것처럼 보이지 않게 한다.
    cache_ttl_sec: int = int(os.getenv("MARKET_CACHE_TTL_SEC", "60"))
    # 캔들·지표는 5분봉 기준이라 틱보다 갱신이 뜸하다. TTL을 따로 준다.
    candle_cache_ttl_sec: int = int(os.getenv("MARKET_CANDLE_TTL_SEC", "3600"))
    indicator_cache_ttl_sec: int = int(os.getenv("MARKET_INDICATOR_TTL_SEC", "300"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
