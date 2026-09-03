<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;

/** `users` 조회. 전 쿼리 Prepared Statement. */
final class UserRepository
{
    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    /** 인증에 필요한 최소 필드만. `password_hash` 는 꺼내지 않는다. */
    public function find(int $id): ?array
    {
        $stmt = $this->db()->prepare(
            'SELECT id, email, name, role, locale FROM users WHERE id = ?'
        );
        $stmt->execute([$id]);
        $row = $stmt->fetch(\PDO::FETCH_ASSOC);

        return $row === false ? null : $row;
    }
}
