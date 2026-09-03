"""AI 호출 예산 — "부를 만한가"가 아니라 "그 돈을 쓸 것인가". (2026-09-03 결정)

`rule_engine.should_request_ai` 는 **시장 조건**을 본다(Tech 70↑/30↓ + 합의 50%↑).
이 모듈은 **비용 정책**을 본다. 둘은 독립이다 — 보는 사람이 아무도 없는데 시장이
요동치면 앞의 것은 통과하고 여기서 막아야 한다. 실측 기준 게이트 통과가 하루 317건,
전부 호출하면 월 $19~37 이 유저 0명인 동안에도 그대로 나간다.

모드:

    off  — 부르지 않는다. 룰 신호는 그대로 나온다 (로컬 개발·테스트용)
    seed — 심볼당 하루 N건만. 적중률 기록을 계속 쌓기 위한 최소량 (기본값)
    full — 게이트를 통과한 신호 전부

**`seed` 를 카운터가 아니라 심볼별 최소 간격으로 만든 이유.**
"하루 25건"을 선착순 카운터로 세면 변동성이 몰리는 시간대에 하루치를 다 써버린다.
실측에서 게이트 통과는 심볼(BTC 28% vs XRP 18%)과 시간대에 크게 몰렸다. 그렇게 쌓인
적중률은 "특정 종목·특정 시간대 한정 통계"라 셀링포인트로 쓸 수 없다(README §9).
간격(24시간 ÷ N)으로 만들면 심볼과 시간대에 저절로 퍼진다.

**모드는 런타임에 바뀐다.** 관리자 화면이 Redis `settings:ai_mode` 를 쓰면 다음 판정부터
즉시 반영된다. `.env` 의 `AI_MODE` 는 그 키가 없을 때의 기본값일 뿐이다 — 환경변수만
보면 끄려고 프로세스를 재시작해야 하는데, 비용이 새는 상황에서 재시작을 기다릴 수 없다.

**조회자가 있으면 seed 모드에서도 full 처럼 동작한다.** 손님이 온 가게만 전등을 켜는
것이 원래 목적이었다. 표시(`watch:{symbol}`)는 Step 6 API / Step 9 대시보드가
`RedisStore.mark_viewer` 로 남긴다 — **지금은 아무도 쓰지 않아 항상 seed 로 동작한다.**
그쪽이 붙으면 이 모듈은 고칠 것이 없다.
"""

from __future__ import annotations

import logging

from trading_engine.config import settings
from trading_engine.market.redis_store import RedisStore

log = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_SEED = "seed"
MODE_FULL = "full"
MODES = (MODE_OFF, MODE_SEED, MODE_FULL)

SECONDS_PER_DAY = 24 * 60 * 60


def resolve_mode(value: str | None) -> str:
    """설정값을 모드로. 모르는 값이면 가장 싼 쪽(seed)으로 물러선다.

    오타 하나로 full 이 되면 조용히 돈이 나간다. 반대 방향의 실수가 낫다.
    """
    mode = (value or "").strip().lower()
    if mode in MODES:
        return mode
    if mode:
        log.warning("AI_MODE=%r 를 알 수 없어 %s 로 동작한다 (%s 중 하나)", value, MODE_SEED, "/".join(MODES))
    return MODE_SEED


class AiBudget:
    """호출 한 건을 쓸지 말지 결정한다."""

    def __init__(
        self,
        store: RedisStore,
        mode: str | None = None,
        seed_calls_per_symbol: int | None = None,
    ) -> None:
        self._store = store
        self._mode = resolve_mode(settings.ai_mode if mode is None else mode)
        self._seed_calls = (
            settings.ai_seed_calls_per_symbol
            if seed_calls_per_symbol is None
            else seed_calls_per_symbol
        )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def enabled(self) -> bool:
        """`.env` 기본값 기준. 런타임 전환은 `allow()` 안의 `current_mode()` 가 본다.

        여기서 Redis 를 읽지 않는 이유는 이 값이 파이프라인의 "봉 차단 키를 쓸지"를
        가르는 동기 판정이기 때문이다. off 로 시작한 서버를 관리자가 켜면 `allow()` 에서
        걸러지고, on 으로 시작한 서버를 끄면 `allow()` 가 False 를 준다 — 어느 쪽이든
        호출은 나가지 않는다.
        """
        return self._mode != MODE_OFF

    @property
    def seed_interval_sec(self) -> int:
        """심볼 하나가 다음 호출까지 기다릴 시간. 하루 5건이면 4.8시간."""
        return SECONDS_PER_DAY // self._seed_calls if self._seed_calls > 0 else 0

    def describe(self) -> str:
        if self._mode == MODE_FULL:
            return "full — 게이트 통과 전부"
        if self._mode == MODE_OFF:
            return "off — 호출하지 않음 (룰 신호만)"
        if self._seed_calls <= 0:
            return "seed — 쿼터 0 (조회 중인 심볼만)"
        return (
            f"seed — 심볼당 하루 {self._seed_calls}건"
            f"(간격 {self.seed_interval_sec / 3600:.1f}시간), 조회 중인 심볼은 제한 없음"
        )

    async def current_mode(self) -> str:
        """지금 적용될 모드. Redis 설정이 있으면 그것이, 없으면 `.env` 기본값이 이긴다.

        조회 실패 시 기본값으로 물러선다 — Redis 가 흔들린다고 예산 정책이 통째로
        사라지면 안 된다.
        """
        try:
            override = await self._store.load_ai_mode()
        except Exception:
            log.exception("AI 모드 설정 조회 실패 - 기본값(%s)을 쓴다", self._mode)
            return self._mode

        return resolve_mode(override) if override else self._mode

    async def allow(self, symbol: str) -> bool:
        """이 심볼에 지금 호출 한 건을 쓸 수 있는지. True 면 슬롯을 소비한 것이다."""
        mode = await self.current_mode()
        if mode == MODE_OFF:
            return False
        if mode == MODE_FULL:
            return True
        if await self._has_viewer(symbol):
            log.debug("%s 조회 중 - seed 쿼터를 쓰지 않고 호출한다", symbol)
            return True
        return await self._claim_seed(symbol)

    async def _has_viewer(self, symbol: str) -> bool:
        try:
            return await self._store.has_viewer(symbol)
        except Exception:
            # 조회자 확인이 안 되면 "없다"로 본다. 못 보는 상태에서 돈을 쓰지 않는다.
            log.exception("조회자 확인 실패 (%s)", symbol)
            return False

    async def _claim_seed(self, symbol: str) -> bool:
        interval = self.seed_interval_sec
        if interval <= 0:
            return False
        try:
            return await self._store.claim_ai_seed(symbol, interval)
        except Exception:
            # Redis 가 흔들릴 때 간격을 못 지키면 예산이 새어나간다. 안 부르는 쪽이 안전하다.
            log.exception("seed 슬롯 확인 실패 - 이번에는 호출하지 않는다 (%s)", symbol)
            return False
