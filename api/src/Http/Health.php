<?php

declare(strict_types=1);

namespace App\Http;

use App\Utils\Database;
use Predis\Client as RedisClient;

/**
 * 헬스체크. 배포 파이프라인(.github/workflows/deploy.yml)이 이 결과로
 * 배포 성공 여부를 판정하므로, 앱이 실제로 의존하는 것만 검사한다.
 */
class Health
{
    public static function check(): array
    {
        $checks = [
            'database' => self::database(),
            'redis'    => self::redis(),
        ];

        $healthy = true;
        foreach ($checks as $result) {
            if (!$result['ok']) {
                $healthy = false;
            }
        }

        return [
            'status'  => $healthy ? 'ok' : 'degraded',
            'env'     => $_ENV['APP_ENV'] ?? 'unknown',
            'mode'    => $_ENV['APP_MODE'] ?? 'unknown',
            'checks'  => $checks,
        ];
    }

    private static function database(): array
    {
        try {
            Database::getConnection()->query('SELECT 1');

            return ['ok' => true, 'detail' => 'connected'];
        } catch (\Throwable $e) {
            return ['ok' => false, 'detail' => self::sanitize($e)];
        }
    }

    private static function redis(): array
    {
        try {
            $client = new RedisClient([
                'scheme' => 'tcp',
                'host'   => $_ENV['REDIS_HOST'] ?? '127.0.0.1',
                'port'   => (int) ($_ENV['REDIS_PORT'] ?? 6379),
                'timeout' => 2.0,
            ]);
            $client->ping();

            return ['ok' => true, 'detail' => 'connected'];
        } catch (\Throwable $e) {
            return ['ok' => false, 'detail' => self::sanitize($e)];
        }
    }

    /**
     * 예외 메시지에 자격증명이 섞여 나가지 않도록, 운영 환경에서는 종류만 노출한다.
     */
    private static function sanitize(\Throwable $e): string
    {
        if (($_ENV['APP_ENV'] ?? 'local') === 'production') {
            return 'connection failed';
        }

        return $e->getMessage();
    }
}
