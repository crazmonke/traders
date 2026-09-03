<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Auth\Jwt;
use App\Repository\AuditRepository;
use App\Repository\UserRepository;
use App\Utils\RateLimiter;
use App\Utils\RedisClient;

/**
 * 회원가입 · 로그인 · 로그아웃. (Step 6-b)
 *
 *   POST /api/v1/auth/register
 *   POST /api/v1/auth/login
 *   POST /api/v1/auth/logout
 *   GET  /api/v1/auth/me
 *
 * **로그아웃이 이 스텝의 핵심이다.** JWT 는 무상태라 발급한 토큰을 되돌릴 수 없는데,
 * 실거래 권한이 걸린 관리자 토큰을 "클라이언트에서 지웠으니 끝"으로 둘 수 없다.
 * 토큰의 `jti` 를 Redis 폐기 목록에 만료 시각까지 올려 서버가 거부하게 한다.
 */
final class Auth
{
    /** 폐기 목록 키 접두사. `Guard` 가 같은 규칙으로 조회한다. */
    public const REVOKED_PREFIX = 'auth:revoked:';

    private const MIN_PASSWORD_LENGTH = 10;
    private const MAX_NAME_LENGTH = 50;

    public function __construct(
        private ?UserRepository $users = null,
        private ?AuditRepository $audit = null,
    ) {
    }

    private function users(): UserRepository
    {
        return $this->users ??= new UserRepository();
    }

    private function audit(): AuditRepository
    {
        return $this->audit ??= new AuditRepository();
    }

    public function handle(string $method, string $subPath): never
    {
        if ($subPath === '/me') {
            $this->me($method);
        }
        if ($method !== 'POST') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'POST 만 지원합니다.', 405);
        }

        // 로그인·가입은 무차별 대입 표적이라 일반 엔드포인트보다 조인다. 유저를 모르니 IP 기준.
        if (!RateLimiter::allow('auth:' . RateLimiter::clientIp(), RateLimiter::AUTH_PER_MINUTE)) {
            Response::error(Response::RATE_LIMITED, '요청이 너무 잦습니다.', 429);
        }

        match ($subPath) {
            '/register' => $this->register(),
            '/login'    => $this->login(),
            '/logout'   => $this->logout(),
            default     => Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404),
        };
    }

    private function register(): never
    {
        $body = Response::jsonBody();
        $email = $this->readEmail($body);
        $password = $this->readPassword($body);
        $name = $this->readName($body);

        $userId = $this->users()->create(
            $email,
            password_hash($password, PASSWORD_DEFAULT),
            $name,
            $this->guessLocale(),
        );
        if ($userId === null) {
            // 이메일 중복. "이미 가입된 이메일"이라고 알려주는 것은 계정 존재 여부를
            // 노출하는 것이지만, 가입 화면에서는 알려주지 않으면 유저가 진행할 수 없다.
            Response::error(Response::INVALID_REQUEST, '이미 가입된 이메일입니다.', 409);
        }

        $this->audit()->record($userId, AuditRepository::REGISTER);

        Response::success([
            'user' => $this->users()->find($userId),
            'plan' => $this->users()->planFor($userId),
            'token' => Jwt::issue($userId),
        ], 201);
    }

    private function login(): never
    {
        $body = Response::jsonBody();
        $email = is_string($body['email'] ?? null) ? trim($body['email']) : '';
        $password = is_string($body['password'] ?? null) ? $body['password'] : '';

        $credentials = $email === '' ? null : $this->users()->credentialsByEmail($email);

        // 존재하지 않는 이메일이어도 해시 검증을 한 번 돌린다. 안 그러면 응답 시간 차이로
        // "가입된 이메일인지"를 알아낼 수 있다.
        $hash = $credentials['password_hash'] ?? '$2y$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinvalidinv';
        $ok = password_verify($password, $hash) && $credentials !== null;

        if (!$ok) {
            Response::error(Response::UNAUTHORIZED, '이메일 또는 비밀번호가 올바르지 않습니다.', 401);
        }

        $userId = (int) $credentials['id'];
        $this->audit()->record($userId, AuditRepository::LOGIN);

        Response::success([
            'user' => $this->users()->find($userId),
            'plan' => $this->users()->planFor($userId),
            'token' => Jwt::issue($userId),
        ]);
    }

    private function logout(): never
    {
        $claims = Jwt::claimsFrom(Jwt::bearerFromHeader(Jwt::requestHeader()));
        if ($claims === null) {
            // 이미 못 쓰는 토큰이다. 성공으로 응답한다 — 로그아웃은 멱등이어야 한다.
            Response::success(['revoked' => false]);
        }

        $revoked = $this->revoke($claims);
        $this->audit()->record($claims['sub'], AuditRepository::LOGOUT);

        Response::success(['revoked' => $revoked]);
    }

    /** @param array{sub:int, jti:?string, exp:int} $claims */
    private function revoke(array $claims): bool
    {
        if ($claims['jti'] === null) {
            // jti 가 없는 옛 토큰. 개별 폐기가 불가능하므로 만료를 기다릴 수밖에 없다.
            error_log('jti 없는 토큰의 로그아웃 요청 - 폐기하지 못했다');
            return false;
        }

        // 남은 수명만큼만 들고 있으면 된다. 만료된 토큰은 서명 검증에서 이미 걸린다.
        $ttl = max(1, $claims['exp'] - time());
        try {
            RedisClient::get()->setex(self::REVOKED_PREFIX . $claims['jti'], $ttl, '1');
        } catch (\Throwable $exception) {
            // 폐기에 실패했는데 성공했다고 답하면, 유저는 로그아웃됐다고 믿지만 토큰은 살아 있다.
            error_log('토큰 폐기 실패: ' . $exception->getMessage());
            Response::error(Response::SERVER_ERROR, '로그아웃을 완료하지 못했습니다.', 500);
        }

        return true;
    }

    private function me(string $method): never
    {
        if ($method !== 'GET') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'GET 만 지원합니다.', 405);
        }
        $guard = new Guard($this->users());
        $user = $guard->user();

        Response::success([
            'user' => $user,
            'plan' => $guard->plan(),
        ]);
    }

    /** @param array<string, mixed> $body */
    private function readEmail(array $body): string
    {
        $email = is_string($body['email'] ?? null) ? trim($body['email']) : '';
        if (filter_var($email, FILTER_VALIDATE_EMAIL) === false || mb_strlen($email) > 191) {
            Response::error(Response::INVALID_REQUEST, '올바른 이메일이 필요합니다.', 400);
        }

        return $email;
    }

    /** @param array<string, mixed> $body */
    private function readPassword(array $body): string
    {
        $password = is_string($body['password'] ?? null) ? $body['password'] : '';
        if (mb_strlen($password) < self::MIN_PASSWORD_LENGTH) {
            Response::error(
                Response::INVALID_REQUEST,
                '비밀번호는 ' . self::MIN_PASSWORD_LENGTH . '자 이상이어야 합니다.',
                400
            );
        }

        return $password;
    }

    /** @param array<string, mixed> $body */
    private function readName(array $body): string
    {
        $name = is_string($body['name'] ?? null) ? trim($body['name']) : '';
        if ($name === '' || mb_strlen($name) > self::MAX_NAME_LENGTH) {
            Response::error(Response::INVALID_REQUEST, '이름이 필요합니다.', 400);
        }

        return $name;
    }

    /** 브라우저 언어로 기본 로케일 추정. (prompt.md [Step 10] 요구사항 3) */
    private function guessLocale(): string
    {
        $header = (string) ($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? '');
        foreach (['ko', 'ja', 'en'] as $locale) {
            if (str_contains(strtolower($header), $locale)) {
                return $locale;
            }
        }

        return 'ko';
    }
}
