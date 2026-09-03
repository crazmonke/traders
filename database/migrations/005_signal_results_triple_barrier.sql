-- ---------------------------------------------------------------------------
-- 005 — 적중률 정의를 삼중 배리어로 통일 (Step 7 착수 전 선행)
--
-- 그전까지 두 곳이 서로 다른 것을 재고 있었다:
--   백테스트  익절 +5% / 손절 -2.5% 중 어디에 먼저 닿았나 (삼중 배리어)
--   적중률    5분 뒤 종가가 진입가 대비 ±0.2% 를 넘었나
-- 같은 신호가 한쪽에서는 성공, 다른 쪽에서는 실패로 기록될 수 있었다.
--
-- ±0.2% 는 왕복 수수료(0.18~0.20%)를 빼면 기댓값이 0 에 수렴하는 정답 정의다.
-- 실제 매매는 익절선·손절선을 걸고 기다리는 것이므로 라벨도 그 모양이어야 한다.
--
-- horizon 을 **늘리되 기존 값을 지우지 않는다.** 배리어까지 걸린 시간이 곧
-- "얼마나 들고 있어야 하는가"이고, 짧은 horizon 이 대부분 TIME_LIMIT 으로 끝난다면
-- 그 신호가 단기용이 아니라는 사실이 데이터로 드러나야 한다.
-- ---------------------------------------------------------------------------
ALTER TABLE `ai_signal_results`
    MODIFY COLUMN `horizon` ENUM('5m', '15m', '1h', '4h', '1d') NOT NULL,
    ADD COLUMN `exit_reason` ENUM('TAKE_PROFIT', 'STOP_LOSS', 'TIME_LIMIT') NULL
        AFTER `return_pct`,
    -- 구간 최고·최저가. 배리어에 얼마나 근접했는지 사후에 볼 수 있어야
    -- 배리어 폭을 재보정할 때 근거가 된다.
    ADD COLUMN `best_price` DECIMAL(18, 8) NULL AFTER `exit_reason`,
    ADD COLUMN `worst_price` DECIMAL(18, 8) NULL AFTER `best_price`;

-- return_pct DECIMAL(6,2) 는 ±9999.99 까지라 익절 5% 를 담기에 충분하다.

-- "이 기간 적중률" 조회가 Step 9 대시보드의 핵심 화면이 된다.
ALTER TABLE `ai_signal_results`
    ADD INDEX `idx_horizon_evaluated` (`horizon`, `evaluated_at`);
