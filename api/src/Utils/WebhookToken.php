<?php

declare(strict_types=1);

namespace App\Utils;

/**
 * 유저별 웹훅 토큰.
 *
 * 이 토큰 하나가 유저를 식별하는 유일한 수단이다(트레이딩뷰 알림에는 로그인이 없다).
 * 그래서 추측이 불가능해야 하고, URL 에 그대로 들어가므로 URL-safe 여야 한다.
 * `random_bytes` 는 암호학적 난수다 — `rand`/`uniqid` 를 쓰면 안 된다.
 *
 * 32바이트를 base64url(패딩 제거)로 쓰면 정확히 43자다.
 * `user_webhooks.webhook_token` 이 CHAR(43)인 이유가 이것이다.
 * (prompt.md v2 [Step 3] 요구사항 1: "URL-safe 랜덤 토큰(32바이트 이상)")
 */
final class WebhookToken
{
    public const BYTES = 32;
    public const LENGTH = 43;

    public static function generate(): string
    {
        return rtrim(strtr(base64_encode(random_bytes(self::BYTES)), '+/', '-_'), '=');
    }

    /** 경로에서 받은 토큰이 우리가 발급한 형식인지. DB 를 조회하기 전에 거른다. */
    public static function isWellFormed(string $token): bool
    {
        return strlen($token) === self::LENGTH
            && preg_match('/^[A-Za-z0-9_-]+$/', $token) === 1;
    }

    /**
     * 유저가 트레이딩뷰 Alert 에 붙여넣을 완성된 URL.
     *
     * 수신은 Python 엔진이 맡으므로(Step 3-b) PHP 와 호스트가 다를 수 있다.
     * 그래서 `WEBHOOK_BASE_URL` 을 따로 두고, 없으면 `APP_URL` 로 물러선다.
     */
    public static function url(string $token): string
    {
        $base = (string) ($_ENV['WEBHOOK_BASE_URL'] ?? $_ENV['APP_URL'] ?? '');

        return rtrim($base, '/') . '/webhook/tv/' . $token;
    }
}
