<?php

declare(strict_types=1);

namespace App\Tests;

use App\Auth\Jwt;
use PHPUnit\Framework\TestCase;

/**
 * JWT 검증 — 실패는 반드시 "인증 안 됨" 쪽으로 떨어져야 한다.
 */
final class JwtTest extends TestCase
{
    private const SECRET = 'test-secret-that-is-long-enough-for-hs256';

    protected function setUp(): void
    {
        $_ENV['JWT_SECRET'] = self::SECRET;
    }

    protected function tearDown(): void
    {
        unset($_ENV['JWT_SECRET']);
    }

    public function testIssuedTokenRoundTrips(): void
    {
        self::assertSame(42, Jwt::userIdFrom(Jwt::issue(42)));
    }

    public function testExpiredTokenIsRejected(): void
    {
        $expired = Jwt::issue(42, -10);

        self::assertNull(Jwt::userIdFrom($expired));
    }

    public function testTokenSignedWithAnotherSecretIsRejected(): void
    {
        $token = Jwt::issue(42);
        $_ENV['JWT_SECRET'] = 'a-completely-different-secret-value-here';

        self::assertNull(Jwt::userIdFrom($token));
    }

    public function testGarbageIsRejected(): void
    {
        self::assertNull(Jwt::userIdFrom(null));
        self::assertNull(Jwt::userIdFrom(''));
        self::assertNull(Jwt::userIdFrom('not-a-jwt'));
        self::assertNull(Jwt::userIdFrom('a.b.c'));
    }

    public function testPlaceholderSecretIsRefused(): void
    {
        /** .env.example 을 그대로 배포한 서버에서 인증이 열리면 안 된다. */
        $_ENV['JWT_SECRET'] = 'change_me_to_a_long_random_string';

        $this->expectException(\RuntimeException::class);
        Jwt::secret();
    }

    public function testShortSecretIsRefused(): void
    {
        $_ENV['JWT_SECRET'] = 'too-short';

        $this->expectException(\RuntimeException::class);
        Jwt::secret();
    }

    public function testMissingSecretIsRefused(): void
    {
        unset($_ENV['JWT_SECRET']);

        $this->expectException(\RuntimeException::class);
        Jwt::secret();
    }

    public function testMisconfiguredSecretIsNotDisguisedAsABadToken(): void
    {
        /**
         * 비밀키 미설정이 401(토큰 오류)로 둔갑하면, 설정이 잘못된 서버에서 모든 요청이
         * "로그인하세요"로 떨어져 원인을 찾기까지 한참 걸린다. 예외가 올라와야 한다.
         */
        $token = Jwt::issue(42);
        $_ENV['JWT_SECRET'] = 'change_me_to_a_long_random_string';

        $this->expectException(\RuntimeException::class);
        Jwt::userIdFrom($token);
    }

    public function testBearerHeaderParsing(): void
    {
        self::assertSame('abc.def.ghi', Jwt::bearerFromHeader('Bearer abc.def.ghi'));
        self::assertSame('abc.def.ghi', Jwt::bearerFromHeader('bearer  abc.def.ghi '));
        self::assertNull(Jwt::bearerFromHeader('Basic abc'));
        self::assertNull(Jwt::bearerFromHeader('abc.def.ghi'));
        self::assertNull(Jwt::bearerFromHeader(null));
        self::assertNull(Jwt::bearerFromHeader('Bearer'));
    }
}
