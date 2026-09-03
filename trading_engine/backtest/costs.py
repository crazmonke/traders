"""매매 비용 모델 — 수수료와 슬리피지. (prompt.md v2 [Step 4] 요구사항 4)

**두 백테스트의 비용이 다르다.** 신호 검증용(`GLOBAL_CONSENSUS`)은 "전략 자체가 되는가"를
보는 것이라 참고용 평균 수수료를 쓰고, 업비트 실전용은 실제로 낼 돈(0.05% × 2 + 슬리피지
0.05%)을 쓴다. 그래서 결과를 하나로 합치면 안 된다 — 같은 전략도 숫자가 달라진다.

Step 4-a 는 `GLOBAL_CONSENSUS` 프로파일만 쓴다. `UPBIT` 값은 스펙에 명시된 수치라 미리
적어 두되, 실전용 백테스트 경로 자체는 Step 4-b 가 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL_CONSENSUS = "GLOBAL_CONSENSUS"


@dataclass(frozen=True)
class CostModel:
    """비율은 모두 소수(0.0005 = 0.05%)."""

    fee_rate: float
    slippage_rate: float
    label: str

    def buy_price(self, price: float) -> float:
        """슬리피지는 항상 불리한 쪽으로. 살 때는 비싸게 체결된다."""
        return price * (1.0 + self.slippage_rate)

    def sell_price(self, price: float) -> float:
        return price * (1.0 - self.slippage_rate)

    def buy_cost(self, price: float, quantity: float) -> float:
        """실제 지출 = 체결가 × 수량 + 수수료."""
        gross = self.buy_price(price) * quantity
        return gross * (1.0 + self.fee_rate)

    def sell_proceeds(self, price: float, quantity: float) -> float:
        """실제 수령 = 체결가 × 수량 - 수수료."""
        gross = self.sell_price(price) * quantity
        return gross * (1.0 - self.fee_rate)


# 신호 검증용. 스펙의 "참고용 평균 수수료".
# 5개 거래소 공개 taker 수수료가 0.04~0.1% 대라 중간값 0.06% 를 쓴다. 슬리피지는
# 글로벌 가중 평균가 자체가 여러 호가를 섞은 값이라 단일 거래소보다 작게 잡는다.
# **이 숫자로 실전 수익을 추정하면 안 된다.** 그건 업비트 실전용(Step 4-b) 몫이다.
REFERENCE = CostModel(fee_rate=0.0006, slippage_rate=0.0003, label=GLOBAL_CONSENSUS)

# 업비트 실전용. 스펙에 못 박힌 수치다 (요구사항 4). Step 4-b 에서 쓴다.
UPBIT = CostModel(fee_rate=0.0005, slippage_rate=0.0005, label="upbit")

PROFILES: dict[str, CostModel] = {
    GLOBAL_CONSENSUS: REFERENCE,
    "upbit": UPBIT,
}


def for_reference_exchange(reference_exchange: str) -> CostModel:
    """`backtest_logs.reference_exchange` 값에 맞는 비용 모델.

    모르는 거래소면 참고용 모델로 물러서지 않고 예외를 던진다. 잘못된 비용으로
    "수익이 났다"는 결과를 내는 것이 조용히 넘어가는 것보다 위험하다.
    """
    try:
        return PROFILES[reference_exchange]
    except KeyError as error:
        raise ValueError(
            f"비용 모델이 정의되지 않은 reference_exchange 다: {reference_exchange!r}"
        ) from error
