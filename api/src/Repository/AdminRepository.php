<?php

declare(strict_types=1);

namespace App\Repository;

use App\Auth\Plan;
use App\Utils\Database;

/** 관리자 화면이 쓰는 조회·변경. 전 쿼리 Prepared Statement. */
final class AdminRepository
{
    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    /**
     * 유저 목록 + 현재 등급.
     *
     * 구독을 LEFT JOIN 으로 붙여 N+1 조회를 피한다. 등급 판정 자체는 `Plan` 이 하므로
     * 만료·해지 규칙이 API 와 화면에서 갈라지지 않는다.
     */
    public function users(int $limit = 200): array
    {
        $stmt = $this->db()->prepare(
            'SELECT u.id, u.email, u.name, u.role, u.locale, u.created_at,
                    s.plan, s.status, s.ends_at
               FROM users u
               LEFT JOIN subscriptions s
                 ON s.id = (SELECT id FROM subscriptions
                             WHERE user_id = u.id ORDER BY ends_at DESC LIMIT 1)
              ORDER BY u.id DESC LIMIT ?'
        );
        $stmt->bindValue(1, $limit, \PDO::PARAM_INT);
        $stmt->execute();

        return array_map(static function (array $row): array {
            $row['plan'] = Plan::fromSubscription([
                'plan' => $row['plan'],
                'status' => $row['status'],
                'ends_at' => $row['ends_at'],
            ]);
            unset($row['status']);
            return $row;
        }, $stmt->fetchAll(\PDO::FETCH_ASSOC));
    }

    /**
     * 등급 수동 부여. 기존 활성 구독은 해지 처리하고 새로 만든다.
     *
     * 행을 덮어쓰지 않는 이유는 "언제 어떤 등급이었는가"가 남아야 하기 때문이다 —
     * 나중에 결제(Step 13)를 붙일 때 이 이력이 대조 기준이 된다.
     */
    public function setPlan(int $userId, string $plan, int $days): bool
    {
        $exists = $this->db()->prepare('SELECT 1 FROM users WHERE id = ?');
        $exists->execute([$userId]);
        if ($exists->fetchColumn() === false) {
            return false;
        }

        $db = $this->db();
        $db->beginTransaction();
        try {
            $db->prepare(
                "UPDATE subscriptions SET status = 'canceled'
                  WHERE user_id = ? AND status = 'active'"
            )->execute([$userId]);

            // FREE 는 구독을 만들지 않는다. 구독이 없으면 Plan 이 FREE 로 판정한다.
            if ($plan !== Plan::FREE) {
                $db->prepare(
                    "INSERT INTO subscriptions (user_id, plan, status, starts_at, ends_at)
                     VALUES (?, ?, 'active', NOW(), NOW() + INTERVAL ? DAY)"
                )->execute([$userId, $plan, $days]);
            }
            $db->commit();
        } catch (\Throwable $exception) {
            $db->rollBack();
            throw $exception;
        }

        return true;
    }

    /** 운영 지표. 화면 상단 요약에 쓴다. */
    public function counts(): array
    {
        $row = $this->db()->query(
            'SELECT (SELECT COUNT(*) FROM users) AS users,
                    (SELECT COUNT(*) FROM ai_signals) AS signals,
                    (SELECT COUNT(*) FROM ai_signals WHERE created_at >= NOW() - INTERVAL 1 DAY)
                        AS signals_24h,
                    (SELECT COUNT(*) FROM backtest_logs) AS backtests,
                    (SELECT COUNT(*) FROM external_signals) AS webhook_signals'
        )->fetch(\PDO::FETCH_ASSOC);

        return array_map('intval', $row ?: []);
    }
}
