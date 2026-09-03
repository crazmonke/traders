<?php

declare(strict_types=1);

namespace App\Utils;

use Predis\Client;

/**
 * Redis 연결 헬퍼. 엔진이 써 둔 캐시(`global:*`, `consensus:*`)를 읽고,
 * 백테스트 작업 큐에 넣는 데 쓴다.
 *
 * **PHP 는 Redis 에 쓰기를 최소한만 한다.** 시세·신호 캐시는 엔진이 주인이고,
 * API 가 끼어들어 쓰면 둘 중 누가 쓴 값인지 알 수 없게 된다.
 */
final class RedisClient
{
    private static ?Client $client = null;

    public static function get(): Client
    {
        if (self::$client === null) {
            $options = [
                'scheme' => 'tcp',
                'host'   => $_ENV['REDIS_HOST'] ?? '127.0.0.1',
                'port'   => (int) ($_ENV['REDIS_PORT'] ?? 6379),
            ];
            if (($_ENV['REDIS_PASSWORD'] ?? '') !== '') {
                $options['password'] = $_ENV['REDIS_PASSWORD'];
            }
            self::$client = new Client($options);
        }

        return self::$client;
    }

    /** JSON 값을 배열로. 키가 없거나 깨졌으면 null. */
    public static function json(string $key): ?array
    {
        $raw = self::get()->get($key);
        if (!is_string($raw) || $raw === '') {
            return null;
        }
        $decoded = json_decode($raw, true);

        return is_array($decoded) ? $decoded : null;
    }
}
