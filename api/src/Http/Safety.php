<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Repository\SafetyRepository;

/**
 * 실거래 안전장치 조회·변경. (Step 6 DoD)
 *
 *   GET   /api/v1/safety/state
 *   PATCH /api/v1/safety/state
 *
 * **매매 엔진(Step 5)보다 이 스위치가 먼저 존재한다.** 그래야 엔진이 붙는 순간부터
 * kill switch 와 한도가 이미 걸려 있다.
 *
 * 유저 본인 것만 다룬다. 관리자가 남의 상태를 제어하는 화면은 Step 9-b 다.
 */
final class Safety
{
    /** PATCH 로 바꿀 수 있는 필드. 이 목록에 없는 키는 무시가 아니라 400 이다. */
    private const EDITABLE = [
        'mode',
        'daily_loss_limit_pct',
        'max_position_size_krw',
        'kill_switch_active',
    ];

    public function __construct(
        private ?SafetyRepository $repository = null,
        private ?Guard $guard = null,
    ) {
    }

    private function repo(): SafetyRepository
    {
        return $this->repository ??= new SafetyRepository();
    }

    public function handle(string $method, string $subPath): never
    {
        if ($subPath !== '/state') {
            Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
        }
        $userId = ($this->guard ??= new Guard())->userId();

        if ($method === 'GET') {
            Response::success(['safety' => $this->repo()->forUser($userId)]);
        }
        if ($method !== 'PATCH') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'GET 또는 PATCH 만 지원합니다.', 405);
        }

        $changes = $this->readChanges(Response::jsonBody());
        Response::success(['safety' => $this->repo()->update($userId, $changes)]);
    }

    /**
     * 변경 요청을 검증한다.
     *
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     */
    private function readChanges(array $body): array
    {
        $unknown = array_diff(array_keys($body), self::EDITABLE);
        if ($unknown !== []) {
            // 조용히 무시하면 "바꿨다고 생각했는데 안 바뀐" 상태가 된다.
            // 실거래 설정에서 그건 위험하다.
            Response::error(
                Response::INVALID_REQUEST,
                '바꿀 수 없는 항목입니다: ' . implode(', ', $unknown),
                400
            );
        }

        $changes = [];
        if (array_key_exists('mode', $body)) {
            $mode = is_string($body['mode']) ? strtoupper(trim($body['mode'])) : '';
            if (!in_array($mode, SafetyRepository::MODES, true)) {
                Response::error(Response::INVALID_REQUEST, 'mode 는 PAPER 또는 LIVE 입니다.', 400);
            }
            $changes['mode'] = $mode;
        }
        if (array_key_exists('kill_switch_active', $body)) {
            $changes['kill_switch_active'] = $this->readBool($body['kill_switch_active']);
        }
        if (array_key_exists('daily_loss_limit_pct', $body)) {
            $changes['daily_loss_limit_pct'] = $this->readAmount(
                $body['daily_loss_limit_pct'], 'daily_loss_limit_pct', 0.01, 100.0
            );
        }
        if (array_key_exists('max_position_size_krw', $body)) {
            $changes['max_position_size_krw'] = $this->readAmount(
                $body['max_position_size_krw'], 'max_position_size_krw', 0.0, 9999999999999999.0
            );
        }

        return $changes;
    }

    private function readBool(mixed $value): int
    {
        if (is_bool($value)) {
            return $value ? 1 : 0;
        }
        if ($value === 0 || $value === 1) {
            return (int) $value;
        }
        Response::error(Response::INVALID_REQUEST, 'kill_switch_active 는 true/false 입니다.', 400);
    }

    private function readAmount(mixed $value, string $field, float $min, float $max): float
    {
        if (!is_int($value) && !is_float($value)) {
            Response::error(Response::INVALID_REQUEST, "$field 는 숫자여야 합니다.", 400);
        }
        $number = (float) $value;
        if ($number < $min || $number > $max) {
            Response::error(Response::INVALID_REQUEST, "$field 값이 범위를 벗어났습니다.", 400);
        }

        return $number;
    }
}
