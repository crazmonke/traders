"""환경 변수 로딩. 저장소 루트의 .env 를 읽는다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# 거래소 무관 심볼 (prompt.md v2 [Step 1] 요구사항 2). 견적통화는 거래소마다 다르므로
# 여기서는 base 만 쓰고 exchange_registry 가 BTC/USDT · BTC/KRW 로 바꾼다.
DEFAULT_SYMBOLS = ["BTC", "ETH", "XRP", "SOL", "DOGE"]

# ccxt 로 수집할 거래소 (ccxt id). prompt.md v2 [Step 1] 요구사항 1 의 5개 전부.
DEFAULT_EXCHANGES = ["binance", "okx", "bybit", "coinbase", "upbit"]


def _split(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    redis_host: str = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str | None = os.getenv("REDIS_PASSWORD") or None
    symbols: list[str] = field(
        default_factory=lambda: _split(os.getenv("SYMBOLS"), DEFAULT_SYMBOLS)
    )
    exchanges: list[str] = field(
        default_factory=lambda: _split(os.getenv("EXCHANGES"), DEFAULT_EXCHANGES)
    )
    # Redis 캐시 TTL(초). 엔진이 죽었을 때 낡은 시세가 살아있는 것처럼 보이지 않게 한다.
    cache_ttl_sec: int = int(os.getenv("MARKET_CACHE_TTL_SEC", "60"))
    # 캔들·지표는 5분봉 기준이라 틱보다 갱신이 뜸하다. TTL을 따로 준다.
    candle_cache_ttl_sec: int = int(os.getenv("MARKET_CANDLE_TTL_SEC", "3600"))
    indicator_cache_ttl_sec: int = int(os.getenv("MARKET_INDICATOR_TTL_SEC", "300"))
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_name: str = os.getenv("DB_DATABASE", "ai_trading")
    db_user: str = os.getenv("DB_USERNAME", "ai_trading")
    db_password: str = os.getenv("DB_PASSWORD", "")
    # AI 호출 예산 모드 — off | seed | full. 자세한 근거는 strategy/ai_budget.py.
    # 기본값을 seed 로 둔다. 유저가 없는 동안 full 로 도는 것이 지금 유일한 고정비다.
    ai_mode: str = os.getenv("AI_MODE", "seed").strip().lower() or "seed"
    # seed 모드에서 심볼 하나에 허용할 하루 호출 수. 5 × 5심볼 = 25건/일.
    ai_seed_calls_per_symbol: int = int(os.getenv("AI_SEED_CALLS_PER_SYMBOL", "5"))
    # 조회자 표시의 수명(초). 대시보드가 이 주기보다 자주 갱신해야 "보고 있다"가 유지된다.
    ai_viewer_ttl_sec: int = int(os.getenv("AI_VIEWER_TTL_SEC", "600"))
    # 트레이딩뷰 웹훅 수신 (Step 3-b). 0 이나 빈 host 면 서버를 띄우지 않는다 —
    # 이 기능을 꺼도 Step 1·2 수집·신호는 그대로 돌아야 한다.
    webhook_enabled: bool = os.getenv("WEBHOOK_ENABLED", "1").strip() not in ("0", "false", "")
    webhook_host: str = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "8100"))
    # 토큰 하나가 분당 보낼 수 있는 요청 수.
    webhook_rate_per_min: int = int(os.getenv("WEBHOOK_RATE_PER_MIN", "60"))
    # 수신 시각 기준 이 시간 안의 같은 심볼 ai_signals 만 참고 연결한다(분).
    webhook_link_window_min: int = int(os.getenv("WEBHOOK_LINK_WINDOW_MIN", "15"))
    # 뉴스 수집 주기(초). RSS 는 분 단위로도 충분하고, 너무 잦으면 매체에 실례다.
    news_poll_sec: int = int(os.getenv("NEWS_POLL_SEC", "300"))
    fred_api_key: str | None = os.getenv("FRED_API_KEY") or None
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
