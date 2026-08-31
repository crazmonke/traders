좋습니다. 지금까지 이야기한 방향을 기준으로 **실제로 사업으로 만든다고 가정한 설계**를 잡아보겠습니다.

제가 권하는 최종 형태는:

> **“AI가 코인을 대신 거래해주는 서비스”가 아니라,
> “실시간 가상자산 시장을 AI와 수학적 모델로 분석해서 매매 판단에 필요한 근거를 제공하는 SaaS”**

입니다.

그리고 **자동매매 엔진은 본인만 사용**합니다. 고객은 분석 결과만 봅니다.

이렇게 하면 개발 난이도도 낮아지고, 고객 API Key를 보관하지 않아도 되며, 나중에 앱으로 확장하기도 좋습니다.

---

# 1. 최종 사업 모델

서비스를 가칭 **AI Trading**이라고 하겠습니다.

![Image](https://images.openai.com/static-rsc-4/NPNFWEquUaI0pYoZufKVNtAIFBv3Cnug4KjZdC7ZjXgKVujInzpphQ7hpjjPl9jEWkAvUjrIBw3nYEfcillpnBl7kWeORa3ZFXLoiE0rB-X5kM_LtWJtG9FjjIpsTrvNupQKeKrrVceBBd02ime6ivGioGkoi20oKIBuNGg4j7qecbqHvOMnDoRaW7Dkiv3C?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/uayDsAO-6Tg9v3pzI2oUbm7_miaDpnDFF_t5us4rf-kb_FesTzORThW0l-pxN1NA3a8CGXQmnb9_UUmsZ7FYH_07tUiKbko1Ycs3xfFlpBhKSBfT0s-5jytGocyaqMrcyvuZjVYPXUHDDdGcCzRMTvnMts3knZSVbhF6LDXYgU9uDepd7g5Kb2Sf9Vqlf_R9?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/Gg5othFr2pB9X-FPtxKOaGfB7gzAp1bvJI8H0FfWCiuwIBBS-YtpL6mp4drDsc1jvto-YU414OTK-QXVlm3xkque0WaNJ25SleYmrt-uRBaqx5WdGUBBml1hNRaCKm4lB8rmWnKt9WGaWkGC0Ysdo3vDnPUyj3uiOXFXiLSBkLdpbbBndxXfW1ws0HYa4Cyf?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/6QRv7jjPglr1UcrccmYqcCHkbE3S-sfv1X60H_0inuDxaN176wkVxzdWNQxKjGvnyw4iuKQmbyql3wtKswjWbhfI_I4QppCjoo5Q7sRRW6RLvds8UHWfAPlLwApWckUWKHHkDjmN4HRYyf5aJhSP-7I-SnzJzqb3wwZL0ExBo0TUNbGluVtoL2i1K3WqYQtQ?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/Zodhwuftt33j3f-Zn6T0NtKG4WcMxaan9nT8mckeh_0Nz59ZDgs6ykzafjvpvmXrNLlAUB0wrzJwjWByEBbEOekKah8qtnomQZ7lZ4YpKkQAHvyNEIzU2t8Lm0ZzZ3WcaQiXJCK6Ep7faoQvtM4JVObxbWVvmP2HXs_01l2nX6U6LM2pkwxZ1tzd7gbQ4WMw?purpose=fullsize)

![Image](https://images.openai.com/static-rsc-4/XMIowGE5winWHT_sW4_E9hno_41Vrjk6hpm2pvohQ06dhSe3-Y_d7OE8vcfadvq5WqP52BWLceiEDM9KIQkWlEypubRfmbSZRY-9G87fkRYor5y1SZ4F4lHdGP2-Cu_tUv-iOnVkzvu59rJUZEGd2DwKbC276N03B6sdXwcJ9r8hhvG6PkXrq1zJ_sdNP-1L?purpose=fullsize)

### 고객이 보는 것

```text
BTC/KRW

현재가       154,200,000원
AI Score          82 / 100

단기 추세          상승
거래량             강세
RSI                52
MACD               골든크로스
변동성             보통

━━━━━━━━━━━━━━━━━━━━

5분   상승 가능성  78%
15분  상승 가능성  71%
1시간 상승 가능성  56%

━━━━━━━━━━━━━━━━━━━━

🟢 BUY SIGNAL

주요 근거
① 거래량 증가
② MACD 골든크로스
③ 단기 이동평균 정배열
④ RSI 과매수 아님

주의 요소
① 장기 추세는 중립
② 변동성 확대 가능성
```

여기서 중요한 것은 **AI가 단순히 BUY라고 하는 것이 아니라 "왜" 그런 판단을 했는지를 보여주는 것**입니다.

---

# 2. 전체 시스템 아키텍처

사용자님이 PHP/MySQL/JS에 익숙하다는 것을 고려하면 **전체를 Python으로 만들 필요가 없습니다.**

제가 추천하는 구성은:

```text
                    ┌──────────────────┐
                    │     사용자        │
                    │ PC / 모바일 웹    │
                    └────────┬─────────┘
                             │ HTTPS
                             ↓
                    ┌──────────────────┐
                    │   Nginx           │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ PHP API Server    │
                    │                  │
                    │ 회원             │
                    │ 결제             │
                    │ 권한             │
                    │ 분석 결과 조회    │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
          MySQL           Redis       Python Engine
              ↑              ↑              │
              │              │              ↓
              │              │       ┌──────────────┐
              │              │       │ Upbit API    │
              │              │       └──────────────┘
              │              │
              │              ↓
              │        실시간 데이터
              │
              ↓
       분석/신호/거래 기록
```

### 기술 스택

| 영역       | 기술                        |
| -------- | ------------------------- |
| Frontend | HTML + JS/jQuery 또는 React |
| Backend  | **PHP**                   |
| DB       | **MySQL/MariaDB**         |
| 실시간 캐시   | Redis                     |
| AI/트레이딩  | **Python**                |
| 시세       | Upbit REST + WebSocket    |
| AI       | OpenAI API                |
| 서버       | Nginx + Linux             |
| 백그라운드    | Supervisor/systemd        |
| 결제       | 국내 PG 또는 SaaS 결제 서비스      |
| 모바일      | 초기에는 반응형 웹                |
| 앱        | 나중에 Flutter               |

사용자님에게는 이게 가장 자연스럽습니다.

---

# 3. Python Engine이 핵심입니다

실제 "두뇌"는 Python입니다.

```text
trading_engine/
│
├── market/
│   ├── upbit_rest.py
│   ├── upbit_websocket.py
│   └── market_manager.py
│
├── indicators/
│   ├── rsi.py
│   ├── macd.py
│   ├── moving_average.py
│   ├── bollinger.py
│   └── volume.py
│
├── strategy/
│   ├── momentum.py
│   ├── mean_reversion.py
│   └── signal_engine.py
│
├── ai/
│   ├── analyzer.py
│   ├── prompt.py
│   └── scorer.py
│
├── backtest/
│   ├── engine.py
│   └── simulator.py
│
├── trading/
│   ├── order_manager.py
│   ├── position_manager.py
│   └── risk_manager.py
│
├── database/
│   └── repository.py
│
└── main.py
```

이 구조로 만들면 나중에 AI 모델을 바꾸거나 전략을 추가하기 쉽습니다.

---

# 4. 업비트 데이터 수집

업비트는 REST API뿐 아니라 WebSocket으로 체결·호가 등의 실시간 데이터를 제공하므로, 실시간 분석에는 WebSocket을 사용하는 것이 적합합니다. ([업비트 개발자 센터][1])

예를 들어:

```text
BTC
ETH
XRP
SOL
DOGE
```

등을 감시합니다.

### 수집 데이터

```text
현재가
체결가
체결량
거래량
호가
매수/매도 잔량
1분봉
3분봉
5분봉
15분봉
1시간봉
4시간봉
```

그리고 Python이 이것을 가공합니다.

---

# 5. AI에게 원시 데이터를 그대로 보내면 안 됩니다

예를 들어 현재 BTC 가격이 얼마인지 AI에게만 던지는 것은 의미가 없습니다.

Python이 먼저 계산합니다.

```text
RSI              52.31
MACD             +1234
MACD Signal      +980
MA5              상승
MA20             상승
MA60             중립
Volume Change    +37%
Volatility       0.84
Order Imbalance  +21%
5m Return        +0.41%
15m Return       +0.72%
```

그리고 이것을 AI에게 전달합니다.

---

# 6. AI는 "판단 + 설명"을 담당

AI에게 이런 구조화된 데이터를 전달합니다.

```json
{
  "symbol": "KRW-BTC",
  "timeframe": "5m",
  "price": 154200000,
  "rsi": 52.31,
  "macd": 1234,
  "volume_change": 37.2,
  "ma_trend": "bullish",
  "volatility": 0.84,
  "order_imbalance": 21.4,
  "return_5m": 0.41,
  "return_15m": 0.72
}
```

그리고 AI가 반드시 **JSON 형식**으로 반환하게 합니다.

```json
{
  "signal": "BUY",
  "score": 82,
  "up_probability": 0.78,
  "sideways_probability": 0.16,
  "down_probability": 0.06,
  "reasons": [
    "거래량 증가",
    "MACD 골든크로스",
    "단기 이동평균 상승"
  ],
  "risks": [
    "장기 추세 불확실",
    "변동성 확대 가능성"
  ]
}
```

이렇게 하면 PHP에서 화면에 쉽게 표시할 수 있습니다.

---

# 7. 그런데 AI만 믿으면 안 됩니다

이게 가장 중요합니다.

저라면:

**AI 100%**

구조를 절대 만들지 않습니다.

대신:

```text
기술적 지표
      ↓
Rule Engine
      ↓
AI
      ↓
Risk Engine
      ↓
최종 Signal
```

로 합니다.

예를 들어:

```text
RSI                 20%
MACD                20%
Trend               20%
Volume              15%
Orderbook           10%
Momentum             5%
AI                   10%
────────────────────────
총점                100%
```

처럼 **AI를 전체 시스템의 일부로 사용**합니다.

---

# 8. 오히려 이것이 이 서비스의 핵심 기술이 됩니다

고객에게는:

> **AI Score 82**

라고 보여주지만 내부적으로는:

```text
Technical Score = 78
AI Score        = 86
Risk Score      = 81

Final Score     = 82
```

처럼 계산하는 것입니다.

그리고 이것을 나중에 실제 결과와 비교합니다.

---

# 9. 백테스트 시스템

이 서비스에서 **굉장히 중요한 기능**입니다.

사용자가 전략을 선택합니다.

```text
BTC/KRW

기간
2025-01-01 ~ 2026-08-28

전략
AI Momentum

초기자금
10,000,000원

수수료
실제 업비트 수수료 적용

슬리피지
0.05%

[ 백테스트 ]
```

결과:

```text
초기자금          10,000,000
최종자산          13,842,000

수익률             +38.42%
승률                61.4%
거래횟수              842
평균수익              0.31%
평균손실             -0.24%
최대낙폭            -11.3%
```

여기서 **수수료와 슬리피지를 반드시 반영**해야 합니다.

---

# 10. "AI 예측 적중률" DB를 별도로 만드세요

이게 사업적으로 굉장히 중요합니다.

예를 들어 AI가:

```text
2026-08-28 13:00

BTC
5분 후 상승 확률 78%
```

이라고 판단했다면 그 순간을 DB에 기록합니다.

그리고 5분 뒤:

```text
실제 결과
+0.43%
```

를 기록합니다.

그러면 시간이 지나면서:

```text
AI 예측 성능

전체 신호          15,283

상승 적중           10,471
하락 적중            2,104
오판                 2,708

적중률               68.4%
```

을 계산할 수 있습니다.

**과거에 발생한 신호를 사후에 골라내는 것이 아니라, 신호가 발생한 순간부터 결과를 기록하는 구조**여야 합니다.

---

# 11. DB 구조

최소한 다음 정도는 필요합니다.

```text
users
subscriptions
payments

markets
market_ticks
candles

indicators
ai_signals
ai_signal_results

strategies
backtests
backtest_trades

alerts

system_logs
api_logs
```

예를 들어 `ai_signals`:

```text
id
market
timeframe
signal
score
up_probability
down_probability
rsi
macd
volume_change
price
reason_json
risk_json
created_at
```

그리고 `ai_signal_results`:

```text
id
signal_id
price_at_signal
price_after_5m
price_after_15m
price_after_1h
return_5m
return_15m
return_1h
result
```

이렇게 하면 나중에 **AI 모델 성능을 검증**할 수 있습니다.

---

# 12. Redis가 필요한 이유

실시간 데이터는 MySQL에 계속 때려 넣으면 비효율적입니다.

그래서:

```text
Upbit WebSocket
       ↓
     Redis
       ↓
Python Engine
       ↓
MySQL
```

로 합니다.

Redis에는:

```text
BTC:price
BTC:orderbook
BTC:5m
ETH:price
...
```

등을 저장합니다.

MySQL에는 **영구적으로 필요한 데이터**만 기록합니다.

---

# 13. PHP는 무엇을 담당하나?

사용자님이 익숙한 PHP를 그대로 활용합니다.

### PHP

```text
회원가입
로그인
구독
결제
회원권한
AI 결과 조회
백테스트 요청
전략 저장
알림 설정
관리자
```

### Python

```text
시장 데이터
지표 계산
AI
백테스트
신호 생성
본인 자동매매
```

이렇게 역할을 분리합니다.

---

# 14. 본인 자동매매는 완전히 별도

이 부분은 특히 분리해야 합니다.

```text
                Python Engine
                     │
             ┌───────┴───────┐
             ↓               ↓
       Analysis Engine   Private Trader
             │               │
          고객용            본인만
             │               │
          DB 저장          Upbit 주문
```

**고객 서비스 서버와 본인 자동매매의 API Key를 논리적으로 분리**하는 것이 좋습니다.

본인 API Key는 서버 환경변수/Secret Manager에 보관하고, 출금 권한은 주지 않는 방향이 좋습니다.

업비트도 API Key와 인증정보를 외부에 노출하지 말고 환경변수 등을 활용해 안전하게 관리하라고 안내하고 있습니다. 또한 현재 API Key는 등록 IP에서만 사용 가능하고, Key는 1년간 유효하며 연장 대신 재발급이 필요합니다. ([업비트 개발자 센터][2])

---

# 15. 업비트 API 비용

여기서는 **API 사용료 자체보다 호출량 설계가 중요**합니다.

현재 업비트 API에는 Rate Limit이 있습니다. 시세 조회 그룹은 API 종류에 따라 초당 10회, Exchange 기본 그룹은 초당 30회, 주문 그룹은 현재 초당 12회입니다. ([업비트 개발자 센터][3])

그래서 고객 1명마다 업비트 REST API를 호출하는 방식은 피합니다.

### 잘못된 구조

```text
고객 1
 → BTC API

고객 2
 → BTC API

고객 3
 → BTC API

...
```

### 올바른 구조

```text
               Upbit
                 │
             단일 수집
                 ↓
              Redis
                 ↓
          Python Engine
                 ↓
           AI Signal
                 ↓
       ┌─────────┼─────────┐
       ↓         ↓         ↓
      고객1      고객2      고객3
```

**시세 데이터는 한 번 수집해서 여러 고객에게 공유**합니다.

이렇게 해야 서버 비용도 낮아집니다.

---

# 16. AI API 비용도 상당히 낮출 수 있습니다

현재 OpenAI의 GPT-5.6 Luna는 **입력 100만 토큰당 $0.20, 출력 100만 토큰당 $1.20**입니다. GPT-5.6 Terra는 $2/$12, Sol은 $4/$20입니다. ([OpenAI Developers][4])

그래서 이 서비스에서는:

### 실시간 신호 생성

**Luna**

### 중요한 종합 분석

**Terra**

### 사람이 읽는 고급 분석/리포트

**Sol 또는 Terra**

정도로 분리하는 것을 추천합니다.

특히 **매초 AI 호출은 절대 하지 않습니다.**

---

# 17. 예를 들어 AI 호출 전략

```text
시장 데이터
    ↓
로컬 지표 계산
    ↓
조건 충족?
    │
    ├─ NO → AI 호출 안 함
    │
    └─ YES
         ↓
       AI 호출
         ↓
      신호 생성
```

예를 들어 BTC, ETH, XRP, SOL, DOGE 5개를 감시하면서 **신호가 발생했을 때만 AI를 호출**합니다.

이렇게 하면 AI API 비용을 상당히 낮출 수 있습니다.

---

# 18. 고객용 화면

제가 실제 서비스라면 메뉴를 이렇게 구성합니다.

```text
┌───────────────────────────────┐
│ AI Trading                    │
├───────────────────────────────┤
│                               │
│ Dashboard                     │
│                               │
│ 🔥 Strong Signals             │
│                               │
│ BTC      🟢 BUY      82       │
│ ETH      🟢 BUY      76       │
│ XRP      🟡 HOLD     54       │
│ SOL      🔴 SELL     31       │
│                               │
├───────────────────────────────┤
│ 시장분석                       │
│ 백테스트                       │
│ AI Signal                     │
│ 전략                           │
│ 알림                           │
│ 내 구독                        │
└───────────────────────────────┘
```

---

# 19. "Strong Signal"이 핵심 화면

예를 들어:

### 🔥 Strong BUY

**BTC/KRW**

```text
AI Score       91
5분 상승확률    84%
15분 상승확률   79%

신호 강도       매우 강함
```

그리고:

**왜?**

```text
✓ 거래량 +47%
✓ MACD 골든크로스
✓ MA5 > MA20
✓ 매수호가 우위
✓ RSI 55
```

**위험요소**

```text
△ 변동성 증가
△ 장기추세 미확정
```

이런 식입니다.

---

# 20. 알림 서비스

유료화한다면 알림이 상당히 중요합니다.

예:

```text
🔔 BTC 강한 매수 신호

AI Score 87

5분 상승확률 81%

거래량 +42%
MACD 골든크로스
```

알림 채널:

* 웹 Push
* 이메일
* 카카오톡 알림톡
* Telegram
* 향후 모바일 Push

등으로 확장할 수 있습니다.

---

# 21. 무료 / 유료 모델

처음부터 너무 비싸게 잡지 않는 것을 추천합니다.

### FREE

```text
BTC/ETH
기본 시장정보
하루 AI 분석 5회
```

### BASIC

**9,900원/월**

```text
전체 코인
AI Signal
실시간 알림
기본 백테스트
```

### PRO

**29,900원/월**

```text
AI 상세분석
전체 백테스트
전략 저장
다중 시간대 분석
AI Signal 성과
고급 알림
```

### 향후

**49,900~99,000원**

고급 전략/데이터/분석 기능이 충분히 검증된 뒤 고려.

---

# 22. 단, "수익률"을 상품의 핵심으로 내세우지 마세요

예를 들어:

> **AI가 최근 1년간 183% 수익!**

이런 마케팅은 상당히 위험합니다.

차라리:

> **최근 30일 AI 신호 3,842건의 실제 결과를 공개합니다.**

처럼 **검증 가능한 통계**를 보여주는 것이 좋습니다.

그리고 반드시:

> 과거 백테스트 및 신호 성과는 미래 수익을 보장하지 않습니다.

라는 성격의 고지를 명확히 해야 합니다.

---

# 23. 법률/정책 설계

여기는 개발 전에 한번 검토해야 합니다.

특히 다음 단계로 구분하세요.

| 기능             | 판단      |
| -------------- | ------- |
| 시세 표시          | 🟢      |
| 기술지표 제공        | 🟢      |
| 과거 데이터 분석      | 🟢      |
| 백테스트           | 🟢      |
| AI 시장 설명       | 🟡      |
| BUY/SELL 신호    | 🟡      |
| 유료 BUY/SELL 신호 | 🟠      |
| 개인별 투자 추천      | 🔴 법률검토 |
| 고객 API Key 수집  | 🔴      |
| 고객 대신 자동주문     | 🔴      |
| 고객 자산 운용       | 🔴      |

특히 **BUY/SELL 신호를 유료로 제공하는 서비스가 국내 법률상 어떤 서비스에 해당하는지는 출시 전에 반드시 전문가 검토**를 권합니다.

그리고 업비트 Open API도 단순히 API가 공개되어 있다고 해서 상용 서비스에서 원하는 방식으로 자유롭게 재판매할 수 있다는 의미는 아닙니다. 업비트는 Open API 이용약관 및 개발자센터의 사용 방법·쿼터를 준수해야 하고, 약관 위반이나 비정상적인 이용 등에 대해 서비스/API 제한을 할 수 있다고 명시합니다. ([업비트 개발자 센터][5])

---

# 24. 사업자 측면

유료 서비스라면 최소한:

```text
사업자등록
전자상거래 관련 검토
개인정보처리방침
이용약관
환불정책
구독약관
마케팅 동의
개인정보 수집 동의
```

등을 준비해야 합니다.

그리고 서비스 내용에 따라 **금융 관련 등록/신고 대상인지**를 전문가에게 확인합니다.

이건 개발 완료 후가 아니라 **MVP 출시 전에 확인하는 것이 좋습니다.**

---

# 25. 보안

고객에게 업비트 API Key를 받지 않는 모델이라면 훨씬 편해집니다.

고객 DB에는:

```text
이름
이메일
비밀번호 해시
구독정보
결제정보 식별자
```

정도만 저장.

업비트 Secret Key는 **고객에게 받지 않습니다.**

본인 자동매매 Key만:

```text
.env
Secret Manager
```

등으로 관리합니다.

---

# 26. 서버 사양

처음에는 굉장히 큰 서버가 필요하지 않습니다.

### MVP

```text
2~4 vCPU
8GB RAM
100~200GB SSD
```

정도로 시작할 수 있습니다.

구성:

```text
Nginx
PHP-FPM
Python
MySQL
Redis
Supervisor
```

정도.

고객이 늘어나면:

```text
Web Server
     ↓
Load Balancer
     ↓
API Server × N
     ↓
Redis
     ↓
Trading Engine
     ↓
MySQL
```

으로 확장합니다.

---

# 27. 개발 기간

AI 코딩을 적극적으로 활용한다는 전제로 잡겠습니다.

### Phase 0 — 기획/규제 검토

**3~5일**

* 서비스 범위
* 약관
* 업비트 API 사용 범위
* 금융규제 검토
* 가격 정책

---

### Phase 1 — 자동매매 Engine

**7~10일**

* Upbit WebSocket
* REST API
* 캔들
* 지표
* 전략
* 가상매매
* 주문 Engine

---

### Phase 2 — 백테스트

**5~7일**

* 과거 데이터
* 수수료
* 슬리피지
* 전략별 결과
* 차트
* 성과 분석

---

### Phase 3 — AI

**5~7일**

* Prompt
* Structured Output
* Score
* Signal
* 설명
* 위험요소
* AI 성과 추적

---

### Phase 4 — 웹서비스

**7~10일**

* 회원가입
* 로그인
* Dashboard
* Signal
* 백테스트
* 전략
* 알림
* 마이페이지

---

### Phase 5 — 결제

**3~5일**

* 상품
* 구독
* 결제
* 결제 검증
* 만료
* 환불
* 권한

---

### Phase 6 — 운영/보안

**5~7일**

* 로그
* 모니터링
* 장애복구
* API 제한
* DB 백업
* 보안
* 관리자

---

## 총 개발기간

### **MVP: 약 4~5주**

### **실제 유료 서비스 수준: 약 6~8주**

AI 코딩을 잘 활용하면 **6주 전후로 첫 번째 유료 베타를 목표**로 잡을 수 있다고 봅니다.

다만 **AI의 매매 성능을 검증하는 기간은 별개**입니다.

코드를 6주 만에 만들었다고 해서:

> "AI가 돈을 번다."

를 6주 만에 증명할 수 있는 것은 아닙니다.

---

# 28. 그래서 제가 가장 추천하는 일정

### 1~2주차

**Python Trading Engine**

```text
Upbit
 ↓
WebSocket
 ↓
지표
 ↓
Signal
 ↓
가상매매
```

---

### 3주차

**Backtest**

```text
과거 1~2년
 ↓
전략별 테스트
 ↓
수수료 반영
 ↓
최적 전략 찾기
```

---

### 4주차

**AI**

```text
Rule Engine
 ↓
AI
 ↓
Score
 ↓
설명
```

---

### 5주차

**Web**

```text
PHP
MySQL
Redis
Dashboard
```

---

### 6주차

**결제 + 알림 + 관리자**

↓

### 7~8주차

**실제 베타 테스트**

```text
본인
↓
지인 5~10명
↓
20~50명
```

---

# 29. 비용은 어느 정도인가?

초기에는 상당히 저렴하게 시작할 수 있습니다.

대략:

| 항목       |          초기 예상 |
| -------- | -------------: |
| 서버       |       월 3~10만원 |
| DB/Redis |      서버에 같이 구성 |
| AI API   |      월 수천원~수만원 |
| 도메인      |        연 1~3만원 |
| SSL      |             무료 |
| 업비트 API  | **API 이용료 없음** |
| 결제 PG    |    거래 발생 시 수수료 |
| 이메일/알림   |        사용량에 따라 |

특히 AI 비용은 호출 구조에 따라 크게 달라집니다.

현재 GPT-5.6 Luna의 API 가격은 입력 100만 토큰 $0.20, 출력 100만 토큰 $1.20이므로, **실시간 신호용으로 작은 입력을 보내고 결과를 짧게 받는 구조라면 초기에는 AI 비용 자체가 큰 부담이 되지 않을 가능성이 높습니다.** ([OpenAI Developers][4])

---

# 30. 이 사업의 진짜 핵심

사실 **AI가 아니라 "신호의 신뢰성"**입니다.

사용자는 결국 이것을 궁금해합니다.

> "그래서 이 신호가 얼마나 맞는데?"

그래서 서비스의 핵심 지표를:

```text
AI Score
Signal 발생 횟수
5분 적중률
15분 적중률
1시간 적중률
평균 상승폭
평균 하락폭
최대 손실
```

로 잡는 게 좋습니다.

---

# 31. 장기적으로는 이런 서비스가 됩니다

```text
                    AI TRADING
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
     실시간 분석       백테스트          AI 전략
        │                │                │
        ↓                ↓                ↓
      Signal          Strategy          Model
        │                │                │
        └────────────────┼────────────────┘
                         ↓
                  성과 데이터 축적
                         ↓
                   AI 모델 개선
                         ↓
                  더 정확한 Signal
```

이렇게 **사용자가 많아질수록 신호 데이터와 성능 데이터가 쌓이는 구조**를 만드는 것이 장기적으로 중요합니다.

---

# 32. 그리고 저는 한 가지를 더 추가하고 싶습니다

처음부터 **"AI가 미래 가격을 예측한다"**고 하지 않는 겁니다.

대신:

> **"현재와 유사한 시장 상황에서 과거에는 어떤 결과가 발생했는가?"**

를 보여주는 기능입니다.

예를 들어:

```text
현재 상황과 유사한 과거 사례

총 2,341건

5분 후 상승      71.8%
평균 변동         +0.34%
15분 후 상승      64.2%
평균 변동         +0.48%
```

이 데이터를 AI가 설명합니다.

이렇게 하면 **AI의 설명 + 통계적 근거**가 같이 존재합니다.

단순 LLM에게 코인 차트를 보여주고:

> "살까?"

라고 묻는 서비스보다 훨씬 제대로 된 트레이딩 시스템이 됩니다.

---

# 제가 실제로 만든다면 최종 기술 구조는 이것입니다

```text
                         INTERNET
                             │
                             ▼
                    ┌────────────────┐
                    │ NGINX / HTTPS  │
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │ PHP API        │
                    │                │
                    │ Auth           │
                    │ Subscription   │
                    │ Dashboard API  │
                    └───┬────────┬───┘
                        │        │
                ┌───────▼───┐ ┌─▼────────┐
                │ MySQL     │ │ Redis    │
                └───────┬───┘ └────┬─────┘
                        │           │
                        └─────┬─────┘
                              │
                     ┌────────▼────────┐
                     │ Python Engine   │
                     │                 │
                     │ Market          │
                     │ Indicators      │
                     │ Strategy        │
                     │ AI              │
                     │ Backtest        │
                     │ Risk            │
                     └───────┬─────────┘
                             │
                     ┌───────▼────────┐
                     │ Upbit API      │
                     │ REST/WebSocket │
                     └────────────────┘

                     [PRIVATE ONLY]
                             │
                             ▼
                     내 업비트 계정
                       자동매매
```

---

## 그리고 사업 진행 순서는 이렇게 하겠습니다

**1. Python으로 내 계정용 자동매매/가상매매 엔진부터 만든다.**

↓

**2. 최소 1~2개월 실제 시장 데이터를 축적한다.**

↓

**3. AI 없이도 수익성 있는 기본 전략을 만든다.**

↓

**4. AI를 붙여 신호 설명/스코어링을 한다.**

↓

**5. 과거 신호의 실제 결과를 자동 기록한다.**

↓

**6. PHP + MySQL로 웹서비스를 만든다.**

↓

**7. 무료 베타를 운영한다.**

↓

**8. AI 신호 성능을 공개적으로 검증한다.**

↓

**9. 금융규제/유사투자자문 해당 여부 및 업비트 상용 이용 범위를 전문가와 최종 확인한다.**

↓

**10. 월 구독 서비스 출시**

↓

**11. 사용자가 충분히 확보되면 앱 개발**

---

### 제 판단

이 아이디어는 **개발 프로젝트로는 충분히 현실적**입니다.

특히 사용자님은 PHP/MySQL/Linux를 이미 다루고 있고 AI 코딩도 활용할 수 있으므로, **기술적으로 가장 어려운 부분은 "코드를 만드는 것"이 아니라 "실제로 의미 있는 매매 신호를 만드는 것"**입니다.

그리고 **처음부터 고객 자동매매까지 욕심내지 않고, 본인만 자동매매 + 고객에게는 검증 가능한 AI 분석/백테스트/신호 통계 제공**으로 시작하는 것이 현재 구상에서는 가장 합리적입니다.

또 하나 중요한 점은 **현재 업비트 API 자체는 실제로 웹 서비스·모바일 앱 등에서 시세/잔고/주문 등을 연결할 수 있는 구조로 제공되고 있지만, 이용약관과 API 사용량 제한을 준수해야 한다는 것**입니다. ([업비트 개발자 센터][5])

그리고 나중에 앱으로 확장할 경우에는 Apple이 현재 **금융 거래·투자·자금관리에 사용하는 앱은 해당 금융 서비스를 제공하는 금융기관이 제출하고 필요한 권한을 보유해야 한다고 명시**하고 있기 때문에, 지금처럼 **웹 SaaS → 나중에 앱** 순서로 가는 것이 특히 합리적입니다. ([Apple Developer][6])

**다음 실제 개발 단계에서는 제가 이 설계를 바로 `V1 개발명세서`로 쪼개는 것을 추천합니다.** 즉 `폴더 구조 → MySQL 전체 테이블 CREATE SQL → Python 모듈 → Upbit WebSocket 수집 → AI JSON 스키마 → PHP API 목록 → 관리자 화면 → 개발 순서`까지 만들어 놓으면, 그 문서를 그대로 **Copilot/Codex에 단계별로 넣어서 실제 개발을 시작할 수 있는 수준**까지 내려갈 수 있습니다.

[1]: https://docs.upbit.com/kr/kr/?utm_source=chatgpt.com "업비트 개발자 센터"
[2]: https://docs.upbit.com/kr/changelog/order-rate-limit-update?utm_source=chatgpt.com "[안내]업비트 Open API Rate Limit 상향 안내"
[3]: https://docs.upbit.com/kr/kr/reference/rate-limits?utm_source=chatgpt.com "요청 수 제한(Rate Limits)"
[4]: https://developers.openai.com/api/docs/models/gpt-5.6-luna?utm_source=chatgpt.com "GPT-5.6 Luna Model | OpenAI API"
[5]: https://docs.upbit.com/kr/kr/page/?utm_source=chatgpt.com "New Page"
[6]: https://developer.apple.com/app-store/review/guidelines/?utm_source=chatgpt.com "App Review Guidelines - Apple Developer"

---

## 로컬 개발 환경 설정 (Local Setup)

### 필요 프로그램 (설치 완료됨)

| 도구 | 용도 | 확인 명령 |
|---|---|---|
| Python 3.12 (`python@3.12`) | trading_engine 실행 | `/opt/homebrew/bin/python3.12 --version` |
| PHP 8.5 / Composer | api 실행 | `php --version`, `composer --version` |
| Colima + Docker + Docker Compose | 로컬 MySQL/Redis/컨테이너 구동 | `docker info`, `docker compose version` |
| Redis (brew) | `redis-cli`로 로컬 디버깅 | `redis-cli ping` |
| mysql-client (brew, keg-only) | `mysql` CLI 접속 | `~/.zshrc`에 PATH 추가됨 (새 터미널부터 적용) |

Colima는 로그인 시 자동 시작되지 않으므로, 컴퓨터를 재부팅했다면 아래로 다시 켜야 합니다.

```bash
colima start
```

### 프로젝트 구조

```
trading_engine/   # Python 분석/매매 엔진 (Step 1~7 구현 대상)
api/              # PHP REST API (Step 5 구현 대상)
database/init.sql # MySQL 스키마 (docker-compose 최초 기동 시 자동 반영)
deploy/           # 운영 서버 배포용 nginx/systemd/스크립트 템플릿
docker-compose.yml
```

### 최초 셋업

```bash
cp .env.example .env   # 값 채우기 (Upbit/OpenAI 키, JWT_SECRET 등)

# Python 엔진
cd trading_engine
/opt/homebrew/bin/python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt

# PHP API
cd ../api
composer install
```

### 로컬 전체 스택 기동 (MySQL + Redis + PHP API + Python Engine)

```bash
docker compose up -d --build
curl http://localhost:8080/          # PHP API 확인
docker compose logs -f python-engine # Python 엔진 로그 확인
docker compose down                  # 종료 (데이터는 volume에 유지)
```

Python 엔진만 로컬에서 직접 실행하며 개발하고 싶다면 MySQL/Redis만 컨테이너로 띄우고, Python은 venv로 실행하는 방식도 가능합니다.

```bash
docker compose up -d mysql redis
cd trading_engine && ./venv/bin/python main.py
```

### 배포 (운영 서버)

운영 서버는 **upsignal.mycafe24.com** (Ubuntu 24.04)이며, **호스트 네이티브 + Docker 하이브리드** 구성입니다.

`main` 브랜치에 푸시하면 [.github/workflows/deploy.yml](.github/workflows/deploy.yml)이
**테스트 → rsync 동기화 → 의존성 설치 → 서비스 재시작 → 헬스체크** 순으로 자동 배포합니다.

```
push to main → ci(pytest/composer) → rsync → remote_deploy.sh → https://upsignal.mycafe24.com/ 200 확인
```

- 1회 설정(SSH 키, GitHub Secrets, sudoers, systemd 등록): **[deploy/README.md](deploy/README.md)**
- 수동 배포가 필요하면 `./deploy/scripts/deploy.sh`
- 서버 후처리 로직은 `deploy/scripts/remote_deploy.sh` 한 곳에 모여 있습니다 (자동/수동 배포 공용)

> `.env`(백업 포함), `api/vendor`, `trading_engine/venv`, `docker-compose.override.yml` 은 rsync 제외 대상이라 서버 파일이 그대로 유지됩니다.
> 특히 `.env`는 서버에 직접 만들어야 하며(Upbit 키는 고정 IP 화이트리스트 + 출금 권한 제외), 없으면 배포가 중단됩니다.
> `deploy/nginx/ai-trading.conf`는 Nginx용 참고 템플릿이며 현재 운영 서버는 Apache로 동작합니다.

#### 운영 서버 런타임 구성

전부 Docker도 아니고 전부 네이티브도 아닙니다. 컴포넌트마다 실행 형태가 다르므로 **호스트에서 접근하느냐 컨테이너에서 접근하느냐에 따라 호스트명이 달라집니다.**

| 컴포넌트 | 실행 형태 | 호스트에서 | 컨테이너에서 |
| --- | --- | --- | --- |
| PHP API | 호스트 Apache 2.4 + PHP 8.3<br>DocumentRoot `/var/www/traders/api/public` | — | — |
| MariaDB | 호스트 네이티브 (`127.0.0.1:3306`, 외부 미개방) | `127.0.0.1` | `host.docker.internal` |
| Redis | **Docker 컨테이너** `traders-redis-1` (`redis:7-alpine`)<br>`0.0.0.0:6379` 퍼블리시 | `127.0.0.1` | `redis` |
| Python 매매 엔진 | Docker 컨테이너 `traders-python-engine-1` | — | — |

#### Redis는 Docker 컨테이너를 사용합니다 (호스트에 설치 금지)

Redis는 기획상 **필수 컴포넌트**입니다. `market:KRW-BTC:ticker` / `orderbook` / `candles:5m` 캐싱과
시그널 Pub/Sub 채널(`channel:signals`)이 모두 Redis 위에서 동작합니다.

다만 **호스트에 `apt install redis-server` 로 별도 설치하면 안 됩니다.**
`docker-compose.yml`의 `redis:7-alpine` 컨테이너가 이미 6379 포트를 점유하고 있어서,
호스트 패키지는 포트 충돌로 기동에 실패합니다. (php-redis 확장을 설치할 때 의존 패키지로 딸려 들어오는 경우가 있습니다.)

호스트 패키지가 깔려 있다면 비활성화만 해두면 됩니다. 컨테이너 Redis가 계속 서비스합니다.

```bash
systemctl disable --now redis-server   # 호스트 여분 설치본 정리
redis-cli ping                          # PONG → 컨테이너 Redis 정상
```

> 정리: "Redis를 안 써도 되는 것"이 아니라 **"Redis는 Docker로 이미 쓰고 있고, 호스트용 여분 설치본이 불필요하게 하나 더 깔려 있던 것"** 입니다.

#### `.env`의 호스트명 주의

`.env`는 호스트 PHP API와 컨테이너 Python 엔진이 함께 읽지만, 같은 값이 양쪽에서 통하지 않습니다.
**`.env`에는 호스트 기준 값**을 넣고, **컨테이너 기준 값은 `docker-compose.override.yml`에서 덮어씁니다.**

```bash
# .env - 호스트(Apache/PHP) 기준
DB_HOST=127.0.0.1
REDIS_HOST=127.0.0.1
```

```yaml
# docker-compose.override.yml - 컨테이너(python-engine) 기준으로 덮어쓰기
services:
  python-engine:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      DB_HOST: host.docker.internal
      REDIS_HOST: redis
```

`REDIS_HOST`를 override에 넣지 않으면 컨테이너가 `.env`의 `127.0.0.1`을 그대로 읽어
자기 자신에게 붙으려다 실패합니다.

