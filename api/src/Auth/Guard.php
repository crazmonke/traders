<?php

declare(strict_types=1);

namespace App\Auth;

use App\Auth\Plan;
use App\Http\Auth;
use App\Http\Response;
use App\Repository\UserRepository;
use App\Utils\RedisClient;

/**
 * 인증 가드. 엔드포인트마다 같은 401 처리를 되풀이하지 않기 위해 한 곳에 모은다.
 *
 * Step 3-a 의 `Webhooks` 안에 있던 로직을 꺼낸 것이다. Step 6 에서 엔드포인트가
 * 다섯 개 더 붙으므로, 복사해 두면 인증 규칙이 여섯 군데로 갈라진다.
 */
final class Guard
{
    private ?array $cachedUser = null;

    public function __construct(private ?UserRepository $users = null)
    {
    }

    private function users(): UserRepository
    {
        return $this->users ??= new UserRepository();
    }

    /** 인증된 유저 행. 실패하면 401/500 으로 끝난다. */
    public function user(): array
    {
        if ($this->cachedUser !== null) {
            return $this->cachedUser;
        }

        try {
            $claims = Jwt::claimsFrom(Jwt::bearerFromHeader(Jwt::requestHeader()));
        } catch (\RuntimeException) {
            // JWT_SECRET 미설정. 인증을 열어주는 대신 막는다.
            Response::error(Response::SERVER_ERROR, '인증 설정이 준비되지 않았습니다.', 500);
        }

        if ($claims === null || $this->isRevoked($claims['jti'])) {
            Response::error(Response::UNAUTHORIZED, '유효한 인증 토큰이 필요합니다.', 401);
        }

        $user = $this->users()->find($claims['sub']);
        if ($user === null) {
            // 서명은 맞지만 유저가 지워진 경우. FK 위반으로 500 이 되게 두지 않는다.
            Response::error(Response::UNAUTHORIZED, '유효한 인증 토큰이 필요합니다.', 401);
        }

        return $this->cachedUser = $user;
    }

    /**
     * 로그아웃된 토큰인가.
     *
     * **Redis 가 죽으면 폐기 여부를 알 수 없다.** 이때는 "유효하다"로 넘기지 않고
     * 막는다 — 실거래 권한이 걸린 토큰이라, 폐기된 토큰을 통과시키는 쪽이 훨씬 위험하다.
     * (Rate Limit 과 반대 방향의 판단이다. 그쪽은 막으면 정상 유저가 못 쓸 뿐이다.)
     */
    private function isRevoked(?string $jti): bool
    {
        if ($jti === null) {
            return false;  // jti 없는 옛 토큰. 만료로만 무효화된다.
        }
        try {
            return (bool) RedisClient::get()->exists(Auth::REVOKED_PREFIX . $jti);
        } catch (\Throwable $exception) {
            error_log('토큰 폐기 확인 실패: ' . $exception->getMessage());
            return true;
        }
    }

    /** 이 유저의 등급. 판정 규칙은 `UserRepository::planForUser()` 한 곳에 있다. */
    public function plan(): string
    {
        return $this->users()->planForUser($this->user());
    }

    /** 등급이 모자라면 402 로 끝난다. 화면이 아니라 여기서 막아야 우회가 안 된다. */
    public function requirePlan(string $required): string
    {
        $plan = $this->plan();
        if (!Plan::atLeast($plan, $required)) {
            Response::error(
                Response::PLAN_REQUIRED,
                strtoupper($required) . ' 등급부터 사용할 수 있습니다.',
                402
            );
        }

        return $plan;
    }

    /** user_id 만 필요할 때. */
    public function userId(): int
    {
        return (int) $this->user()['id'];
    }

    /**
     * 관리자만 통과. 아니면 404 로 끝난다.
     *
     * 403 이 아니라 404 인 이유는 웹훅과 같다 — 403 은 "그 경로는 존재한다"를
     * 알려주는 것과 같아서, 관리자 전용 경로의 존재 자체가 노출된다.
     */
    public function admin(): array
    {
        $user = $this->user();
        if (($user['role'] ?? 'user') !== 'admin') {
            Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
        }

        return $user;
    }
}
