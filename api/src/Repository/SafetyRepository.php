<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;

/**
 * `trading_safety_state` — 실거래 안전장치. (Step 5 가 읽고 Step 6 이 보여준다)
 *
 * **스위치를 매매 엔진보다 먼저 만든다.** 순서를 바꾸면 "안전장치가 작동했는지 볼 수 없는
 * 상태로 실거래를 돌리게" 된다(ROADMAP 순서 변경 결정).
 *
 * 유저별 행이다. 남의 안전장치를 보거나 바꿀 수 없도록 모든 쿼리에 `user_id` 가 들어간다.
 */
final class SafetyRepository
{
    public const MODES = ['PAPER', 'LIVE'];

    /** 행이 없을 때 만들어 줄 기본값. 반드시 PAPER 로 시작한다(Step 5 DoD). */
    public const DEFAULT_MAX_POSITION_KRW = 100000.00;

    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    /** 유저의 안전장치 상태. 없으면 PAPER 기본값으로 만들어 돌려준다. */
    public function forUser(int $userId): array
    {
        $row = $this->select($userId);
        if ($row !== null) {
            return $row;
        }

        $stmt = $this->db()->prepare(
            'INSERT INTO trading_safety_state (user_id, mode, max_position_size_krw) '
            . 'VALUES (?, ?, ?)'
        );
        $stmt->execute([$userId, 'PAPER', self::DEFAULT_MAX_POSITION_KRW]);

        return $this->select($userId) ?? [];
    }

    private function select(int $userId): ?array
    {
        $stmt = $this->db()->prepare(
            'SELECT id, user_id, mode, daily_loss_limit_pct, max_position_size_krw,
                    kill_switch_active, updated_at
               FROM trading_safety_state WHERE user_id = ?'
        );
        $stmt->execute([$userId]);
        $row = $stmt->fetch(\PDO::FETCH_ASSOC);

        return $row === false ? null : $this->present($row);
    }

    /**
     * 지정한 필드만 갱신한다.
     *
     * @param array<string, mixed> $changes 허용된 키만 들어온다(호출부에서 검증).
     */
    public function update(int $userId, array $changes): array
    {
        $this->forUser($userId);  // 행이 없으면 먼저 만든다

        if ($changes !== []) {
            $sets = implode(', ', array_map(fn (string $key) => "`$key` = ?", array_keys($changes)));
            $stmt = $this->db()->prepare(
                "UPDATE trading_safety_state SET $sets WHERE user_id = ?"
            );
            $stmt->execute([...array_values($changes), $userId]);
        }

        return $this->select($userId) ?? [];
    }

    private function present(array $row): array
    {
        return [
            'mode'                  => $row['mode'],
            'daily_loss_limit_pct'  => (float) $row['daily_loss_limit_pct'],
            'max_position_size_krw' => (float) $row['max_position_size_krw'],
            'kill_switch_active'    => (bool) $row['kill_switch_active'],
            'updated_at'            => $row['updated_at'],
        ];
    }
}
