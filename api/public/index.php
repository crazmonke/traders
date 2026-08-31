<?php

/**
 * Front controller (placeholder).
 * Step 5 요구사항(회원/구독/시그널/백테스트/세이프티 엔드포인트, JWT 인증,
 * CORS, Rate Limit, Prepared Statement)은 App\ 네임스페이스 하위에 구현합니다.
 */

require __DIR__ . '/../vendor/autoload.php';

$dotenv = Dotenv\Dotenv::createImmutable(__DIR__ . '/../../');
$dotenv->safeLoad();

header('Content-Type: application/json; charset=utf-8');

echo json_encode([
    'success' => true,
    'message' => 'AI Trading API placeholder - implement Step 5 endpoints',
]);
