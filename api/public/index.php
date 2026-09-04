<?php

declare(strict_types=1);

/**
 * Front controller.
 *
 * 정적 파일(HTML/CSS/JS/이미지)은 Apache의 .htaccess 또는
 * PHP 내장 서버가 직접 서빙하고, 그 외 모든 요청이 이 파일로 들어온다.
 *
 * 로컬 미리보기:  composer serve --working-dir=api   (http://127.0.0.1:8080)
 */

// PHP 내장 서버에서도 Apache와 동일하게 정적 파일을 먼저 서빙한다.
if (PHP_SAPI === 'cli-server') {
    $requestPath = urldecode(parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/');
    $target      = __DIR__ . $requestPath;

    if (is_file($target)) {
        return false;
    }

    // 디렉토리는 index.html 을 직접 내려준다.
    // (return false 로 넘기면 내장 서버가 디렉토리 인덱스로 index.php 를
    //  다시 실행해 함수가 중복 선언된다. Apache 의 DirectoryIndex 와 동작을 맞춘다.)
    if (is_dir($target)) {
        $indexHtml = rtrim($target, '/') . '/index.html';
        if (is_file($indexHtml)) {
            header('Content-Type: text/html; charset=utf-8');
            readfile($indexHtml);
            exit;
        }
    }
}

require dirname(__DIR__) . '/vendor/autoload.php';

// .env 는 저장소 루트에 있다 (api/public → api → 저장소 루트)
Dotenv\Dotenv::createImmutable(dirname(__DIR__, 2))->safeLoad();

$path   = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$path   = '/' . trim($path, '/');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

/** 허용된 Origin 에만 CORS 헤더를 내려준다. */
$allowedOrigins = array_filter(array_map(
    'trim',
    explode(',', (string) ($_ENV['CORS_ALLOWED_ORIGINS'] ?? ''))
));
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';

if ($origin !== '' && in_array($origin, $allowedOrigins, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
    header('Access-Control-Allow-Headers: Content-Type, Authorization');
    header('Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS');
}

if ($method === 'OPTIONS') {
    http_response_code(204);
    exit;
}

/** JSON 응답 후 종료. (구 엔드포인트용 - 신규는 App\Http\Response 를 쓴다) */
function respond(array $body, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    exit;
}

// ------------------------------------------------------------------ 라우팅

// 유저별 TradingView 웹훅 관리 (Step 3-a). 하위 경로(/{id}, /{id}/rotate)가 있어
// switch 앞에서 접두사로 먼저 가른다.
const WEBHOOK_ROUTE = '/api/v1/webhooks/tradingview';

// 접두사 → 핸들러. 하위 경로(/{id}, /latest 등)가 있어 switch 앞에서 먼저 가른다.
$prefixRoutes = [
    '/api/v1/auth'         => fn () => new App\Http\Auth(),
    '/api/v1/admin'        => fn () => new App\Http\Admin(),
    WEBHOOK_ROUTE          => fn () => new App\Http\Webhooks(),
    '/api/v1/signals'      => fn () => new App\Http\Signals(),
    '/api/v1/market'       => fn () => new App\Http\Market(),
    '/api/v1/backtest'     => fn () => new App\Http\Backtest(),
    '/api/v1/safety'       => fn () => new App\Http\Safety(),
];

// 분당 요청 제한 (Step 6 DoD). 인증 전이라 IP 기준이고, 로그인·가입은 핸들러가
// 유저 단위로 한 번 더 조인다. 제한 자체가 인증보다 앞서야 무차별 대입을 늦출 수 있다.
if (str_starts_with($path, '/api/v1/') && !App\Utils\RateLimiter::allow(
    'api:' . App\Utils\RateLimiter::clientIp()
)) {
    App\Http\Response::error(App\Http\Response::RATE_LIMITED, '요청이 너무 잦습니다.', 429);
}

foreach ($prefixRoutes as $prefix => $factory) {
    if ($path === $prefix || str_starts_with($path, $prefix . '/')) {
        $factory()->handle($method, substr($path, strlen($prefix)));
    }
}

switch ($path) {
    case '/api/health':
        $health = App\Http\Health::check();
        respond($health, $health['status'] === 'ok' ? 200 : 503);

        // no break (respond 가 종료한다)
    case '/api':
        respond([
            'success'   => true,
            'message'   => 'AI Trading API',
            'endpoints' => [
                '/api/health',
                '/api/v1/auth/register',
                '/api/v1/auth/login',
                '/api/v1/auth/logout',
                '/api/v1/auth/me',
                WEBHOOK_ROUTE,
                '/api/v1/signals/latest',
                '/api/v1/signals/strong',
                '/api/v1/market/summary',
                '/api/v1/backtest/run',
                '/api/v1/backtest/logs',
                '/api/v1/safety/state',
                '/api/v1/admin/accuracy',
            ],
        ]);
}

// 나머지 엔드포인트가 추가되기 전까지 /api/* 는 JSON 404 로 응답한다.
if (str_starts_with($path, '/api/')) {
    App\Http\Response::error(App\Http\Response::NOT_FOUND, '없는 경로입니다.', 404);
}

// 그 외 경로는 정적 페이지가 없는 것이므로 HTML 404.
http_response_code(404);
header('Content-Type: text/html; charset=utf-8');
readfile(__DIR__ . '/404.html');
