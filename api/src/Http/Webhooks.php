<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Jwt;
use App\Repository\WebhookRepository;
use App\Utils\WebhookToken;

/**
 * 유저별 TradingView 웹훅 관리 API. (prompt.md v2 [Step 3] 요구사항 1)
 *
 *   POST   /api/v1/webhooks/tradingview             발급
 *   GET    /api/v1/webhooks/tradingview             내 목록
 *   POST   /api/v1/webhooks/tradingview/{id}/rotate 재발급(폐기 후 발급)
 *   DELETE /api/v1/webhooks/tradingview/{id}        폐기
 *
 * 이 API 는 **우리가 트레이딩뷰 시세를 가져오는 기능이 아니다.** 유저가 자기 계정에서
 * 만든 Alert 를 자기 대시보드로 중계받게 해주는 것뿐이다(prompt.md v2 §5).
 *
 * 남의 웹훅 id 를 넣으면 403 이 아니라 **404** 다. 403 은 "그 id 는 존재한다"를
 * 알려주는 것과 같다.
 */
final class Webhooks
{
    private const MAX_LABEL_LENGTH = 100;

    public function __construct(private ?WebhookRepository $repository = null)
    {
    }

    private function repo(): WebhookRepository
    {
        return $this->repository ??= new WebhookRepository();
    }

    /** 라우터 진입점. 경로는 `/api/v1/webhooks/tradingview` 이후 부분만 받는다. */
    public function handle(string $method, string $subPath): never
    {
        try {
            $this->route($method, $subPath);
        } catch (\Throwable $exception) {
            // DB 가 죽으면 PDOException 이 그대로 올라가 스택 트레이스(DSN·파일 경로 포함)를
            // 200 과 함께 뱉는다. 내부 사정은 로그에만 남기고 응답은 규격대로 500 이다.
            error_log('webhook api 오류: ' . $exception);
            Response::error(Response::SERVER_ERROR, '요청을 처리하지 못했습니다.', 500);
        }
    }

    private function route(string $method, string $subPath): never
    {
        $userId = $this->authenticate();

        if ($subPath === '') {
            match ($method) {
                'POST' => $this->create($userId),
                'GET'  => $this->index($userId),
                default => Response::error(
                    Response::METHOD_NOT_ALLOWED,
                    'GET 또는 POST 만 지원합니다.',
                    405
                ),
            };
        }

        if (preg_match('#^/(\d+)/rotate$#', $subPath, $matches) === 1) {
            if ($method !== 'POST') {
                Response::error(Response::METHOD_NOT_ALLOWED, 'POST 만 지원합니다.', 405);
            }
            $this->rotate($userId, (int) $matches[1]);
        }

        if (preg_match('#^/(\d+)$#', $subPath, $matches) === 1) {
            if ($method !== 'DELETE') {
                Response::error(Response::METHOD_NOT_ALLOWED, 'DELETE 만 지원합니다.', 405);
            }
            $this->revoke($userId, (int) $matches[1]);
        }

        Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
    }

    /** 유효한 JWT 의 user_id. 아니면 401 로 끝난다. */
    private function authenticate(): int
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
        // 토큰 서명은 맞지만 유저가 지워진 경우. FK 위반으로 500 이 되게 두지 않는다.
        if (!$this->repo()->userExists($userId)) {
            Response::error(Response::UNAUTHORIZED, '유효한 인증 토큰이 필요합니다.', 401);
        }

        return $userId;
    }

    private function create(int $userId): never
    {
        $body  = Response::jsonBody();
        $label = $this->readLabel($body);

        Response::success(
            ['webhook' => $this->present($this->repo()->create($userId, $label))],
            201
        );
    }

    private function index(int $userId): never
    {
        $includeRevoked = ($_GET['include_revoked'] ?? '') === '1';
        $rows = $this->repo()->listForUser($userId, $includeRevoked);

        Response::success([
            'webhooks' => array_map(fn (array $row) => $this->present($row), $rows),
        ]);
    }

    private function rotate(int $userId, int $id): never
    {
        $created = $this->repo()->rotate($userId, $id);
        if ($created === null) {
            Response::error(Response::NOT_FOUND, '웹훅을 찾을 수 없습니다.', 404);
        }

        Response::success(
            ['webhook' => $this->present($created), 'revoked_id' => $id],
            201
        );
    }

    private function revoke(int $userId, int $id): never
    {
        if (!$this->repo()->revoke($userId, $id)) {
            Response::error(Response::NOT_FOUND, '웹훅을 찾을 수 없습니다.', 404);
        }

        Response::success(['id' => $id, 'revoked' => true]);
    }

    /** @param array<string, mixed> $body */
    private function readLabel(array $body): ?string
    {
        $label = $body['label'] ?? null;
        if ($label === null) {
            return null;
        }
        if (!is_string($label)) {
            Response::error(Response::INVALID_REQUEST, 'label 은 문자열이어야 합니다.', 400);
        }
        $label = trim($label);
        if ($label === '') {
            return null;
        }
        if (mb_strlen($label) > self::MAX_LABEL_LENGTH) {
            Response::error(
                Response::INVALID_REQUEST,
                'label 은 ' . self::MAX_LABEL_LENGTH . '자를 넘을 수 없습니다.',
                400
            );
        }

        return $label;
    }

    /**
     * 응답용 표현. `user_id` 는 싣지 않는다 — 자기 것만 볼 수 있으므로 알려줄 것이 없다.
     *
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function present(array $row): array
    {
        return [
            'id'               => (int) $row['id'],
            'label'            => $row['label'],
            'url'              => WebhookToken::url((string) $row['webhook_token']),
            'is_active'        => (bool) $row['is_active'],
            'last_received_at' => $row['last_received_at'],
            'created_at'       => $row['created_at'],
            'revoked_at'       => $row['revoked_at'],
        ];
    }
}
