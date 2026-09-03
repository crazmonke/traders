-- ---------------------------------------------------------------------------
-- 003 — backtest_logs 성과 지표 보강 (Step 4)
--
-- Step 4 DoD 는 "총수익률/승률/거래수/손익비/MDD 를 내고 backtest_logs 에 저장한다"를
-- 요구하는데, 001 까지의 스키마에 **손익비(평균 수익 ÷ 평균 손실) 컬럼이 없다.**
-- params_json 에 밀어 넣으면 Step 6 의 백테스트 조회 API 가 정렬·필터를 못 한다.
--
-- 손실 거래가 하나도 없으면 손익비가 정의되지 않으므로 NULL 을 허용한다.
-- 0 으로 채우면 "손실이 없었다"가 "손익비 0(최악)"으로 읽힌다.
-- ---------------------------------------------------------------------------
ALTER TABLE `backtest_logs`
    ADD COLUMN `avg_profit_loss_ratio` DECIMAL(8, 2) NULL AFTER `win_rate`;

-- 신호검증용/실전용을 섞어 보지 않도록 기준별 조회를 인덱스로 받쳐 준다.
ALTER TABLE `backtest_logs`
    ADD INDEX `idx_reference_symbol` (`reference_exchange`, `symbol`, `created_at`);
