"""체결 틱 링버퍼.

초당 수십 건이 들어오므로 마켓별로 collections.deque(maxlen=200) 에 담는다.
(prompt.md 3.1절)
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Iterator

MAX_TICKS = 200


class TickBuffer:
    """마켓별 최근 체결 틱을 고정 길이로 보관한다."""

    def __init__(self, maxlen: int = MAX_TICKS) -> None:
        self._maxlen = maxlen
        self._buffers: dict[str, Deque[dict[str, Any]]] = {}

    def push(self, market: str, tick: dict[str, Any]) -> None:
        buf = self._buffers.get(market)
        if buf is None:
            buf = self._buffers[market] = deque(maxlen=self._maxlen)
        buf.append(tick)

    def recent(self, market: str, count: int | None = None) -> list[dict[str, Any]]:
        buf = self._buffers.get(market)
        if not buf:
            return []
        items = list(buf)
        return items if count is None else items[-count:]

    def __len__(self) -> int:
        return sum(len(buf) for buf in self._buffers.values())

    def __iter__(self) -> Iterator[str]:
        return iter(self._buffers)
