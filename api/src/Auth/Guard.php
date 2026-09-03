<?php

declare(strict_types=1);

namespace App\Auth;

use App\Http\Response;
use App\Repository\UserRepository;

/**
 * 인증 가드. 엔드포인트마다 같은 401 처리를 되풀이하지 않기 위해 한 곳에 모은다.
 *
 * Step 3-a 의 `Webhooks` 안에 있던 로직을 꺼낸 것이다. Step 6 에서 엔드포인트가
 * 다섯 개 더 붙으므로, 복사해 두면 인증 규칙이 여섯 군데로 갈라진다.
 */
final class Guard
{
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
        try {
            $userId = Jwt::userIdFrom(Jwt::bearerFromHeader(Jwt::requestHeader()));
        } catch (\RuntimeException) {
            // JWT_SECRET 미설정. 인증을 열어주는 대신 막는다.
            Response::error(Response::SERVER_ERROR, '인증 설정이 준비되지 않았습니다.', 500);
        }

        if ($userId === null) {
            Response::error(Response::UNAUTHORIZED, '유효한 인증 토큰이 필요합니다.', 401);
        }

        $user = $this->users()->find($userId);
        if ($user === null) {
            // 서명은 맞지만 유저가 지워진 경우. FK 위반으로 500 이 되게 두지 않는다.
            Response::error(Response::UNAUTHORIZED, '유효한 인증 토큰이 필요합니다.', 401);
        }

        return $user;
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
