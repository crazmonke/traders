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

/** JSON 응답 후 종료. */
function respond(array $body, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    exit;
}

// ------------------------------------------------------------------ 라우팅
switch ($path) {
    case '/api/health':
        $health = App\Http\Health::check();
        respond($health, $health['status'] === 'ok' ? 200 : 503);

        // no break (respond 가 종료한다)
    case '/api':
        respond([
            'success'   => true,
            'message'   => 'AI Trading API',
            'endpoints' => ['/api/health'],
        ]);
}

// Step 5 엔드포인트가 추가되기 전까지 /api/* 는 JSON 404 로 응답한다.
if (str_starts_with($path, '/api/')) {
    respond([
        'success' => false,
        'error'   => 'Not Found',
        'path'    => $path,
    ], 404);
}

// 그 외 경로는 정적 페이지가 없는 것이므로 HTML 404.
http_response_code(404);
header('Content-Type: text/html; charset=utf-8');
readfile(__DIR__ . '/404.html');
