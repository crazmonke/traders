<?php

declare(strict_types=1);

namespace App\Auth;

use Firebase\JWT\JWT as FirebaseJwt;
use Firebase\JWT\Key;

/**
 * JWT 발급·검증.
 *
 * Step 6-b(9/11)가 인증 전반을 맡지만, Step 3-a 의 웹훅 발급 API 는 "로그인 유저"를
 * 식별해야 하므로 **검증만 먼저 당겨온다**. 로그인 엔드포인트·갱신 토큰·Rate Limit 은
 * 그대로 6-b 몫이다. `issue()` 는 6-b 의 로그인과 개발·테스트에서 쓴다.
 *
 * `firebase/php-jwt` 는 이미 composer.json 에 있었다.
 */
final class Jwt
{
    public const ALGORITHM = 'HS256';
    public const DEFAULT_TTL_SEC = 3600;

    /** .env.example 의 자리표시자. 이 값이 그대로면 인증을 열지 않는다. */
    private const PLACEHOLDER_SECRET = 'change_me_to_a_long_random_string';

    /** HS256 에 쓸 최소 길이. 짧은 비밀키는 무차별 대입에 취약하다. */
    private const MIN_SECRET_LENGTH = 32;

    /**
     * 서명 비밀키. 설정되지 않았거나 자리표시자면 예외를 던진다.
     *
     * 비밀키가 없을 때 "검증 통과"로 물러서면 인증이 통째로 사라진다.
     * 반드시 실패하는 쪽으로 떨어져야 한다.
     */
    public static function secret(): string
    {
        $secret = (string) ($_ENV['JWT_SECRET'] ?? '');

        if ($secret === '' || $secret === self::PLACEHOLDER_SECRET) {
            throw new \RuntimeException('JWT_SECRET 이 설정되지 않았습니다.');
        }
        if (strlen($secret) < self::MIN_SECRET_LENGTH) {
            throw new \RuntimeException('JWT_SECRET 이 너무 짧습니다 (32자 이상).');
        }

        return $secret;
    }

    /**
     * 유저 하나에 대한 액세스 토큰.
     *
     * `jti`(토큰 고유 id)를 넣는다. JWT 는 무상태라 발급한 토큰을 되돌릴 방법이 없는데,
     * **실거래 권한이 걸린 관리자 토큰**을 "클라이언트에서 지웠으니 끝"으로 둘 수 없다.
     * 로그아웃은 이 `jti` 를 Redis 폐기 목록에 올려 서버가 거부하게 한다(`Guard`).
     */
    public static function issue(int $userId, int $ttlSec = self::DEFAULT_TTL_SEC): string
    {
        $now = time();

        return FirebaseJwt::encode([
            'sub' => $userId,
            'jti' => bin2hex(random_bytes(16)),
            'iat' => $now,
            'exp' => $now + $ttlSec,
        ], self::secret(), self::ALGORITHM);
    }

    /**
     * 토큰의 클레임. 유효하지 않으면 null.
     *
     * @return array{sub:int, jti:?string, exp:int}|null
     */
    public static function claimsFrom(?string $bearerToken): ?array
    {
        if ($bearerToken === null || $bearerToken === '') {
            return null;
        }
        $key = new Key(self::secret(), self::ALGORITHM);

        try {
            $payload = FirebaseJwt::decode($bearerToken, $key);
        } catch (\Throwable) {
            return null;
        }

        $sub = $payload->sub ?? null;
        if (!is_int($sub) && !(is_string($sub) && ctype_digit($sub))) {
            return null;
        }
        if ((int) $sub <= 0) {
            return null;
        }

        return [
            'sub' => (int) $sub,
            'jti' => isset($payload->jti) ? (string) $payload->jti : null,
            'exp' => isset($payload->exp) ? (int) $payload->exp : 0,
        ];
    }

    /**
     * 토큰에서 user_id 를 꺼낸다. 유효하지 않으면 null.
     *
     * 만료·서명 불일치·형식 오류를 구분하지 않는다. 어느 쪽이든 클라이언트가 할 일은
     * "다시 로그인"으로 같고, 구분해서 알려주면 공격자에게 힌트가 된다.
     */
    public static function userIdFrom(?string $bearerToken): ?int
    {
        // 비밀키 조회는 `claimsFrom` 안에서도 try 밖이다. 안에 두면 "JWT_SECRET 미설정"이
        // "토큰이 틀렸다"로 둔갑해, 설정이 잘못된 서버가 500 대신 전부 401 을 돌려준다.
        return self::claimsFrom($bearerToken)['sub'] ?? null;
    }

    /** `Authorization: Bearer xxx` 헤더에서 토큰만 뽑아낸다. */
    public static function bearerFromHeader(?string $header): ?string
    {
        if ($header === null) {
            return null;
        }
        if (preg_match('/^Bearer\s+(\S+)$/i', trim($header), $matches) !== 1) {
            return null;
        }

        return $matches[1];
    }

    /** 현재 요청의 Authorization 헤더. */
    public static function requestHeader(): ?string
    {
        $header = $_SERVER['HTTP_AUTHORIZATION']
            ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION']
            ?? null;

        return is_string($header) ? $header : null;
    }
}
