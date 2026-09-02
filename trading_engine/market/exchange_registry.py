"""거래소 어댑터 레지스트리.

거래소마다 다른 것(ccxt id, 견적통화, 호가 지원 여부)을 여기 한 곳에 모은다.
수집 로직(`exchange_feed.py`)은 거래소 이름을 몰라도 되게 하는 것이 목적이다.
(prompt.md v2 [Step 1] 요구사항 1·8)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ccxt.pro as ccxtpro


@dataclass(frozen=True)
class ExchangeSpec:
    """거래소 한 곳의 고정 속성."""

    code: str  # ccxt id
    display_name: str
    quote: str  # 견적통화. BTC/USDT 의 USDT
    supports_orderbook: bool = True
    is_private_trading_target: bool = False  # 자동매매 실행처 (업비트만 True)

    def symbol(self, base: str) -> str:
        """'BTC' → 'BTC/USDT' 처럼 거래소 표기로 바꾼다."""
        return f"{base}/{self.quote}"


# DB `exchanges` 테이블 시드와 같은 5개.
#
# coinbase 는 ccxt id 를 반드시 'coinbase'(Advanced Trade)로 쓴다.
# 'coinbaseexchange' 는 호가 구독에 API 키를 요구해서(AuthenticationError)
# 공개 수집에 쓸 수 없다. 2026-09-02 실측 확인.
REGISTRY: dict[str, ExchangeSpec] = {
    "binance": ExchangeSpec("binance", "Binance", "USDT"),
    "okx": ExchangeSpec("okx", "OKX", "USDT"),
    "bybit": ExchangeSpec("bybit", "Bybit", "USDT"),
    "coinbase": ExchangeSpec("coinbase", "Coinbase", "USD"),
    "upbit": ExchangeSpec("upbit", "Upbit", "KRW", is_private_trading_target=True),
}


class UnknownExchangeError(KeyError):
    """레지스트리에 없는 거래소 코드."""


def get_spec(code: str) -> ExchangeSpec:
    try:
        return REGISTRY[code]
    except KeyError:
        raise UnknownExchangeError(
            f"모르는 거래소 코드: {code!r} (가능한 값: {', '.join(sorted(REGISTRY))})"
        ) from None


def resolve_specs(codes: list[str]) -> list[ExchangeSpec]:
    """설정에 적힌 거래소 코드를 스펙으로 바꾼다. 하나라도 틀리면 기동 전에 멈춘다."""
    return [get_spec(code) for code in codes]


def create_client(spec: ExchangeSpec, **overrides: Any) -> Any:
    """ccxt.pro 클라이언트를 만든다.

    시세 수집은 공개 엔드포인트라 API 키가 필요 없다. 자동매매용 Private 클라이언트는
    prompt.md v2 [Step 1] 요구사항 8 에 따라 이 모듈과 완전히 분리한다.
    """
    options: dict[str, Any] = {"enableRateLimit": True}
    options.update(overrides)
    return getattr(ccxtpro, spec.code)(options)
