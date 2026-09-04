"""검증 구간 생성 — 겹치지 않는 창을 과거로 늘어놓는다.

### 왜 UTC 자정에 맞추는가

구간 경계를 "지금"에서 세면 실행할 때마다 몇 분씩 밀린다. 그러면 **캐시가 매번
무효가 되고**(같은 질문을 다시 물을 때마다 20분씩 다시 받는다), 어제 돌린 결과와
오늘 돌린 결과를 나란히 놓을 수도 없다. 자정에 맞추면 같은 날 안에서는 같은 구간이다.

### 왜 겹치지 않게 하는가

겹친 구간은 같은 봉을 여러 번 세는 것이라, 부호 검정에서 **독립된 표가 아니다.**
12개 중 11개가 양수여도 실제로는 한 구간을 11번 센 것일 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

DAY_MS = 86_400_000

# 기본 구간 길이(일). 5분봉 14일이면 4,032봉으로, 지표 워밍업 100봉을 빼도 충분하다.
DEFAULT_LENGTH_DAYS = 14

# 기본 구간 수. bybit 파생 데이터가 180일치를 주므로 14일 × 12 = 168일이 그 안에 든다.
DEFAULT_COUNT = 12


@dataclass(frozen=True)
class Window:
    """[since, until) 밀리초 구간."""

    index: int
    since_ms: int
    until_ms: int

    @property
    def label(self) -> str:
        start = (self.index + 1) * DEFAULT_LENGTH_DAYS
        return f"#{self.index + 1}"

    def days_ago(self, now_ms: int) -> tuple[int, int]:
        return (
            round((now_ms - self.until_ms) / DAY_MS),
            round((now_ms - self.since_ms) / DAY_MS),
        )


def utc_midnight(now_ms: int) -> int:
    """직전 UTC 자정. 구간 경계를 여기에 맞춰 캐시를 재사용한다."""
    return now_ms - (now_ms % DAY_MS)


def generate(
    count: int = DEFAULT_COUNT,
    length_days: int = DEFAULT_LENGTH_DAYS,
    now_ms: int | None = None,
) -> list[Window]:
    """최근 것부터 과거로, 겹치지 않는 구간 `count` 개.

    `#1` 이 가장 최근이다. 오늘분(자정 이후)은 아직 안 끝난 구간이라 넣지 않는다.
    """
    import time

    anchor = utc_midnight(now_ms if now_ms is not None else int(time.time() * 1000))
    span = length_days * DAY_MS

    return [
        Window(index=i, since_ms=anchor - (i + 1) * span, until_ms=anchor - i * span)
        for i in range(count)
    ]
