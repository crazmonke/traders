<?php

declare(strict_types=1);

namespace App\Http;

/**
 * 공통 JSON 응답 포맷.
 *
 * 성공: {"success": true, "data": {...}}
 * 실패: {"success": false, "error": {"code": "...", "message": "..."}}
 *
 * Step 6-a 의 "공통 에러 포맷" DoD 가 이 클래스를 그대로 쓰도록 미리 분리해 둔다.
 * 엔드포인트마다 응답 모양이 갈리면 나중에 프런트에서 분기가 늘어난다.
 */
final class Response
{
    /** 에러 코드. 클라이언트가 문자열 비교로 분기할 수 있게 고정한다. */
    public const UNAUTHORIZED = 'UNAUTHORIZED';
    public const NOT_FOUND = 'NOT_FOUND';
    public const INVALID_REQUEST = 'INVALID_REQUEST';
    public const METHOD_NOT_ALLOWED = 'METHOD_NOT_ALLOWED';
    public const SERVER_ERROR = 'SERVER_ERROR';

    private const ENCODE_FLAGS = JSON_UNESCAPED_UNICODE
        | JSON_UNESCAPED_SLASHES
        | JSON_PRETTY_PRINT;

    /** @param array<string, mixed> $data */
    public static function success(array $data, int $status = 200): never
    {
        self::send(['success' => true, 'data' => $data], $status);
    }

    public static function error(string $code, string $message, int $status): never
    {
        self::send([
            'success' => false,
            'error'   => ['code' => $code, 'message' => $message],
        ], $status);
    }

    /** @param array<string, mixed> $body */
    private static function send(array $body, int $status): never
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($body, self::ENCODE_FLAGS);
        exit;
    }

    /**
     * 요청 본문을 JSON 으로 읽는다. 본문이 없으면 빈 배열.
     *
     * @return array<string, mixed>
     */
    public static function jsonBody(): array
    {
        $raw = file_get_contents('php://input');
        if ($raw === false || trim($raw) === '') {
            return [];
        }
        $decoded = json_decode($raw, true);
        if (!is_array($decoded)) {
            self::error(self::INVALID_REQUEST, 'JSON 본문을 해석할 수 없습니다.', 400);
        }

        return $decoded;
    }
}
