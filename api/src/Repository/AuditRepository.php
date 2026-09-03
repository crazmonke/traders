<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;

/**
 * `audit_logs` 기록. (마이그레이션 004)
 *
 * **감사 로그 실패가 본 동작을 막으면 안 되고, 조용히 사라져도 안 된다.**
 * 기록에 실패하면 error_log 에 남기고 진행한다 — 로그를 못 남겼다고 로그인을 거부하면
 * 로그 저장소 장애가 서비스 전면 중단이 된다.
 */
final class AuditRepository
{
    public const LOGIN = 'LOGIN';
    public const LOGOUT = 'LOGOUT';
    public const REGISTER = 'REGISTER';
    public const SAFETY_CHANGE = 'SAFETY_CHANGE';

    public function __construct(private ?\PDO $pdo = null)
    {
    }

    /** @param array<string, mixed>|null $detail */
    public function record(
        ?int $userId,
        string $action,
        ?string $targetType = null,
        ?int $targetId = null,
        ?array $detail = null,
    ): void {
        try {
            $pdo = $this->pdo ??= Database::getConnection();
            $stmt = $pdo->prepare(
                'INSERT INTO audit_logs (user_id, action, target_type, target_id, detail_json, ip)
                 VALUES (?, ?, ?, ?, ?, ?)'
            );
            $stmt->execute([
                $userId,
                $action,
                $targetType,
                $targetId,
                $detail === null ? null : json_encode($detail, JSON_UNESCAPED_UNICODE),
                \App\Utils\RateLimiter::clientIp(),
            ]);
        } catch (\Throwable $exception) {
            error_log("감사 로그 기록 실패 ($action): " . $exception->getMessage());
        }
    }
}
