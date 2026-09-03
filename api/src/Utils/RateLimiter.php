<?php

declare(strict_types=1);

namespace App\Utils;

/**
 * 분당 요청 제한. (Step 6 DoD — 분당 60회)
 *
 * 분 단위 고정 창이라 창 경계에서 최대 2배까지 통과할 수 있다. 대시보드 조회 수준에서는
 * 그 오차가 문제되지 않고, 슬라이딩 윈도우를 쓸 만한 가치가 없다.
 *
 * **Redis 가 죽으면 통과시킨다.** 여기서 막으면 캐시 장애가 곧 서비스 전면 중단이 된다.
 * (Step 3-b 수신 제한과 같은 판단이고, AI 예산 판정과는 반대 방향이다 — 그쪽은 막지 못하면
 * 돈이 나가고, 이쪽은 막으면 정상 유저가 못 쓴다.)
 */
final class RateLimiter
{
    public const DEFAULT_PER_MINUTE = 60;

    /** 로그인·회원가입은 더 조인다. 무차별 대입 시도를 늦추기 위함이다. */
    public const AUTH_PER_MINUTE = 10;

    public static function allow(string $bucket, ?int $limit = null): bool
    {
        $limit ??= (int) ($_ENV['RATE_LIMIT_PER_MINUTE'] ?? self::DEFAULT_PER_MINUTE);
        if ($limit <= 0) {
            return true;
        }

        $key = 'ratelimit:' . $bucket . ':' . floor(time() / 60);
        try {
            $redis = RedisClient::get();
            $count = (int) $redis->incr($key);
            if ($count === 1) {
                $redis->expire($key, 120);
            }
        } catch (\Throwable $exception) {
            error_log('rate limit 확인 실패: ' . $exception->getMessage());
            return true;
        }

        return $count <= $limit;
    }

    /** 인증 전 요청의 버킷. 유저를 모르므로 IP 로 센다. */
    public static function clientIp(): string
    {
        $ip = $_SERVER['REMOTE_ADDR'] ?? 'unknown';

        // 프록시 뒤라면 nginx 가 넣어준 값을 쓴다. 신뢰할 수 있는 프록시가 앞에 있다는
        // 전제이며, 없으면 클라이언트가 헤더를 위조해 제한을 우회할 수 있다.
        $forwarded = $_SERVER['HTTP_X_REAL_IP'] ?? null;

        return is_string($forwarded) && $forwarded !== '' ? $forwarded : (string) $ip;
    }
}
