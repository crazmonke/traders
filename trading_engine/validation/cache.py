"""캔들 로컬 캐시 — 같은 질문을 다시 물을 때 20분을 기다리지 않기 위해.

검증 한 번에 (심볼 × 구간 × 거래소)만큼 캔들을 받는다. 12구간 × 3심볼 × 5거래소면
180번의 페이지네이션 수집이고, 실측으로 20분이 넘었다. 파라미터 하나 바꿔 다시 돌릴
때마다 그 시간을 다시 쓰는 것은 **검증을 안 하게 만드는 가장 확실한 방법**이다.

구간이 UTC 자정에 맞춰져 있으므로(`windows.py`) 캐시 키가 같은 날 안에서는 안정적이다.
과거 구간의 캔들은 바뀌지 않으므로 만료를 두지 않는다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from trading_engine.backtest.runner import collect
from trading_engine.config import ROOT

log = logging.getLogger(__name__)

CACHE_DIR = ROOT / ".cache" / "candles"


def path_for(symbol: str, timeframe: str, since_ms: int, until_ms: int) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}_{since_ms}_{until_ms}.json"


async def candles(
    symbol: str, since_ms: int, until_ms: int, timeframe: str = "5m"
) -> dict[str, list[dict[str, Any]]]:
    """거래소별 캔들. 캐시에 있으면 그것을, 없으면 받아서 저장한다."""
    target = path_for(symbol, timeframe, since_ms, until_ms)
    if target.exists():
        try:
            return json.loads(target.read_text())
        except (json.JSONDecodeError, OSError):
            # 쓰다 만 파일일 수 있다. 지우고 다시 받는다.
            log.warning("캔들 캐시가 깨져 다시 받는다: %s", target.name)
            target.unlink(missing_ok=True)

    fetched = await collect(symbol, since_ms, until_ms, timeframe=timeframe)
    if not fetched:
        # 빈 결과를 캐시하면 일시적 장애가 영구화된다.
        return {}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 같은 파일에 두 프로세스가 쓰지 않도록 임시 파일에 쓰고 옮긴다.
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(fetched))
    temporary.replace(target)

    return fetched


def clear() -> int:
    """캐시를 비운다. 수집 로직을 고쳤을 때 쓴다. 지운 파일 수를 돌려준다."""
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.json"))
    for file in files:
        file.unlink(missing_ok=True)
    return len(files)
