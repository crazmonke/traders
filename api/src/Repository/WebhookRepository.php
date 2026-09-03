<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;
use App\Utils\WebhookToken;

/**
 * `user_webhooks` 접근. 전 쿼리 Prepared Statement.
 *
 * **모든 조회·변경에 `user_id` 조건이 들어간다.** 이 테이블은 유저별 개인 전략 정보와
 * 직결되므로(prompt.md v2 §5 "유저 간 웹훅 신호 공유 금지"), id 만으로 다루는 메서드를
 * 두지 않는다. 남의 id 를 넣으면 "권한 없음"이 아니라 **없는 것처럼** 0건이 나온다 —
 * 존재 여부 자체가 힌트가 되면 안 된다.
 */
final class WebhookRepository
{
    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    public function userExists(int $userId): bool
    {
        $stmt = $this->db()->prepare('SELECT 1 FROM users WHERE id = ? LIMIT 1');
        $stmt->execute([$userId]);

        return $stmt->fetchColumn() !== false;
    }

    /**
     * 새 웹훅을 발급한다. 토큰 충돌은 UNIQUE 제약이 잡는다.
     *
     * @return array<string, mixed>
     */
    public function create(int $userId, ?string $label): array
    {
        $token = WebhookToken::generate();

        $stmt = $this->db()->prepare(
            'INSERT INTO user_webhooks (user_id, webhook_token, label) VALUES (?, ?, ?)'
        );
        $stmt->execute([$userId, $token, $label]);

        $id = (int) $this->db()->lastInsertId();

        return $this->find($userId, $id) ?? [];
    }

    /**
     * 유저 소유의 웹훅 하나. 남의 것이면 null.
     *
     * @return array<string, mixed>|null
     */
    public function find(int $userId, int $id): ?array
    {
        $stmt = $this->db()->prepare(
            'SELECT id, user_id, webhook_token, label, is_active, last_received_at,
                    created_at, revoked_at
               FROM user_webhooks
              WHERE id = ? AND user_id = ?'
        );
        $stmt->execute([$id, $userId]);
        $row = $stmt->fetch(\PDO::FETCH_ASSOC);

        return $row === false ? null : $row;
    }

    /**
     * 유저의 웹훅 목록. 기본은 살아 있는 것만.
     *
     * @return list<array<string, mixed>>
     */
    public function listForUser(int $userId, bool $includeRevoked = false): array
    {
        $sql = 'SELECT id, user_id, webhook_token, label, is_active, last_received_at,
                       created_at, revoked_at
                  FROM user_webhooks
                 WHERE user_id = ?';
        if (!$includeRevoked) {
            $sql .= ' AND is_active = 1 AND revoked_at IS NULL';
        }
        $sql .= ' ORDER BY id DESC';

        $stmt = $this->db()->prepare($sql);
        $stmt->execute([$userId]);

        return $stmt->fetchAll(\PDO::FETCH_ASSOC);
    }

    /**
     * 폐기. 이미 폐기됐거나 남의 것이면 false.
     *
     * 행을 지우지 않는다. `external_signals.user_webhook_id` 가 이 행을 참조하고 있어,
     * 지우면 지난 수신 기록이 어디서 왔는지 알 수 없게 된다.
     */
    public function revoke(int $userId, int $id): bool
    {
        $stmt = $this->db()->prepare(
            'UPDATE user_webhooks
                SET is_active = 0, revoked_at = CURRENT_TIMESTAMP
              WHERE id = ? AND user_id = ? AND revoked_at IS NULL'
        );
        $stmt->execute([$id, $userId]);

        return $stmt->rowCount() > 0;
    }

    /**
     * 재발급 — 기존 것을 폐기하고 새 행을 만든다.
     *
     * 토큰만 갈아끼우지 않는 이유: `external_signals` 가 `user_webhook_id` 로 이 행을
     * 가리키고 있어서, 같은 행의 토큰을 바꾸면 예전에 받은 신호가 새 토큰으로 들어온 것처럼
     * 보인다. 폐기 이력과 수신 이력을 모두 남기려면 새 행이어야 한다.
     *
     * @return array<string, mixed>|null 새 웹훅. 원본이 없거나 남의 것이면 null.
     */
    public function rotate(int $userId, int $id): ?array
    {
        $existing = $this->find($userId, $id);
        if ($existing === null) {
            return null;
        }

        $db = $this->db();
        $db->beginTransaction();
        try {
            $this->revoke($userId, $id);
            $created = $this->create($userId, $existing['label']);
            $db->commit();
        } catch (\Throwable $exception) {
            $db->rollBack();
            throw $exception;
        }

        return $created;
    }
}
