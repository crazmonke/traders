-- ---------------------------------------------------------------------------
-- 007 — AI 없이도 신호를 기록한다 (적중률 데이터 수집을 AI 예산에서 분리)
--
-- 그전까지 신호는 **AI 분석이 붙어야만** 저장됐다. up_prob 등이 NOT NULL 이라
-- AI 없이 저장하려면 없는 확률을 지어내야 했기 때문이다.
--
-- 문제는 AI 가 2026-09-03 부터 **점수에 들어가지 않는다**는 것이다(설명 전용).
-- 그런데 AI 예산(seed = 심볼당 하루 5건)이 저장까지 막고 있어서, 적중률 데이터가
-- 하루 25건으로 묶여 있었다. 실측: 21시간 동안 24건.
--
-- 점수에 영향도 없는 비용 통제가 이 서비스의 유일한 실적 데이터를 12배 이상
-- 줄이고 있었다. 확률·설명 컬럼을 NULL 허용으로 바꿔 둘을 분리한다.
--
--   NULL  = AI 를 부르지 않았다 (예산·게이트)
--   값 있음 = AI 가 답했다
--
-- 빈 배열('[]')이 아니라 NULL 인 이유: '[]' 는 "AI 가 근거가 없다고 답했다"로
-- 읽히지만 실제로는 묻지 않은 것이다. 둘은 다른 사실이다.
--
-- PHP(`SignalRepository::present`)와 화면(`app.js`)은 이미 NULL 을 처리한다.
-- ---------------------------------------------------------------------------
ALTER TABLE `ai_signals`
    MODIFY COLUMN `up_prob` DECIMAL(5, 2) NULL,
    MODIFY COLUMN `sideways_prob` DECIMAL(5, 2) NULL,
    MODIFY COLUMN `down_prob` DECIMAL(5, 2) NULL,
    MODIFY COLUMN `reasons_json` JSON NULL,
    MODIFY COLUMN `risks_json` JSON NULL;
