<?php

declare(strict_types=1);

namespace App\Repository;

use App\Auth\Plan;
use App\Utils\Database;

/**
 * `users` + `subscriptions`. 전 쿼리 Prepared Statement.
 *
 * **`password_hash` 는 인증 검증 외에는 절대 꺼내지 않는다.** 응답 직렬화 대상 배열에
 * 한 번이라도 섞이면 그대로 클라이언트로 나간다.
 */
final class UserRepository
{
    private const PUBLIC_COLUMNS = 'id, email, name, role, locale, created_at';

    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    public function find(int $id): ?array
    {
        return $this->one('SELECT ' . self::PUBLIC_COLUMNS . ' FROM users WHERE id = ?', [$id]);
    }

    public function findByEmail(string $email): ?array
    {
        return $this->one(
            'SELECT ' . self::PUBLIC_COLUMNS . ' FROM users WHERE email = ?', [$email]
        );
    }

    /** 로그인 검증 전용. 해시를 포함하므로 응답에 쓰지 말 것. */
    public function credentialsByEmail(string $email): ?array
    {
        return $this->one('SELECT id, password_hash FROM users WHERE email = ?', [$email]);
    }

    /** @return int 새 user id. 이메일 중복이면 null. */
    public function create(string $email, string $passwordHash, string $name, string $locale): ?int
    {
        try {
            $stmt = $this->db()->prepare(
                'INSERT INTO users (email, password_hash, name, role, locale)
                 VALUES (?, ?, ?, ?, ?)'
            );
            $stmt->execute([$email, $passwordHash, $name, 'user', $locale]);
        } catch (\PDOException $exception) {
            // UNIQUE(email) 위반. 경쟁 조건에서도 여기로 떨어지므로 사전 조회만으로는 부족하다.
            if (($exception->errorInfo[1] ?? 0) === 1062) {
                return null;
            }
            throw $exception;
        }

        return (int) $this->db()->lastInsertId();
    }

    /**
     * 유저의 현재 등급. 구독이 없거나 만료면 FREE, **관리자는 항상 PRO**.
     *
     * 등급 판정 경로를 여기 하나로 둔다. 로그인 응답과 `Guard` 가 각자 계산하면
     * "로그인 직후엔 FREE, 새로고침하면 PRO" 같은 어긋남이 생긴다(실제로 그랬다).
     *
     * @param array<string, mixed> $user `find()` 가 돌려주는 행(`id`, `role` 필요)
     */
    public function planForUser(array $user): string
    {
        // 운영자가 합의율·근거를 못 보면 유저 문의에 답할 수 없다.
        // 관리자는 CLI 로만 만들어지므로 이 우회로 결제를 건너뛸 수는 없다.
        if (($user['role'] ?? 'user') === 'admin') {
            return Plan::PRO;
        }

        return Plan::fromSubscription($this->one(
            'SELECT plan, status, ends_at FROM subscriptions
              WHERE user_id = ? ORDER BY ends_at DESC LIMIT 1',
            [(int) $user['id']]
        ));
    }

    /** id 로 조회한 뒤 등급 판정. 유저가 없으면 FREE. */
    public function planFor(int $userId): string
    {
        $user = $this->find($userId);

        return $user === null ? Plan::FREE : $this->planForUser($user);
    }

    private function one(string $sql, array $params): ?array
    {
        $stmt = $this->db()->prepare($sql);
        $stmt->execute($params);
        $row = $stmt->fetch(\PDO::FETCH_ASSOC);

        return $row === false ? null : $row;
    }
}
