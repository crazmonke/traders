<?php

declare(strict_types=1);

/**
 * 관리자 지정 CLI. (2026-09-03 결정)
 *
 *   php api/bin/make-admin.php kysloving@gmail.com
 *   php api/bin/make-admin.php kysloving@gmail.com --create   # 계정이 없으면 만든다
 *   php api/bin/make-admin.php --list                          # 현재 관리자 목록
 *
 * **관리자 승격을 HTTP 엔드포인트로 두지 않는다.** 그런 API 가 존재하는 것 자체가
 * 권한 상승 취약점이다. 서버 셸에 접근할 수 있는 사람만 실행할 수 있어야 한다.
 *
 * **비밀번호를 명령줄 인자로 받지 않는다.** argv 는 `ps` 프로세스 목록과 셸 히스토리에
 * 그대로 남는다. 표준입력으로만 받고 화면에 찍지 않는다.
 *
 * 승격·강등은 `audit_logs` 에 남는다(마이그레이션 004). 누가 언제 관리자가 됐는지는
 * 실거래 권한과 직결되므로 기록이 없으면 안 된다.
 */

use App\Repository\AuditRepository;
use App\Utils\Database;

if (PHP_SAPI !== 'cli') {
    // api/bin 은 웹 루트(api/public) 밖이라 URL 로 닿지 않지만, 배치가 바뀌어도
    // 웹에서 실행되지 않도록 여기서 한 번 더 막는다.
    http_response_code(404);
    exit(1);
}

require dirname(__DIR__) . '/vendor/autoload.php';
Dotenv\Dotenv::createImmutable(dirname(__DIR__, 2))->safeLoad();

const MIN_PASSWORD_LENGTH = 10;

function fail(string $message): never
{
    fwrite(STDERR, "오류: $message\n");
    exit(1);
}

function usage(): never
{
    fwrite(STDERR, <<<TXT
    사용법:
      php api/bin/make-admin.php <이메일>            계정을 관리자로 승격
      php api/bin/make-admin.php <이메일> --create   계정이 없으면 만들고 승격
      php api/bin/make-admin.php --list              현재 관리자 목록

    비밀번호는 인자로 받지 않는다. --create 일 때 표준입력으로 묻는다.

    TXT);
    exit(1);
}

/** 입력을 화면에 찍지 않고 받는다. 어깨너머로 보이면 안 된다. */
function readSecret(string $prompt): string
{
    fwrite(STDOUT, $prompt);
    if (function_exists('shell_exec') && stripos(PHP_OS_FAMILY, 'win') === false) {
        shell_exec('stty -echo 2>/dev/null');
    }
    $value = rtrim((string) fgets(STDIN), "\r\n");
    shell_exec('stty echo 2>/dev/null');
    fwrite(STDOUT, "\n");

    return $value;
}

$args = array_slice($argv, 1);
if ($args === []) {
    usage();
}

$pdo = Database::getConnection();
$audit = new AuditRepository($pdo);

if ($args[0] === '--list') {
    $rows = $pdo->query(
        "SELECT id, email, name, created_at FROM users WHERE role = 'admin' ORDER BY id"
    )->fetchAll(PDO::FETCH_ASSOC);

    if ($rows === []) {
        fwrite(STDOUT, "관리자가 없습니다.\n");
        exit(0);
    }
    fwrite(STDOUT, "관리자 " . count($rows) . "명:\n");
    foreach ($rows as $row) {
        fwrite(STDOUT, sprintf("  #%-4s %-32s %s\n", $row['id'], $row['email'], $row['name']));
    }
    exit(0);
}

$email = trim($args[0]);
$create = in_array('--create', $args, true);

if (filter_var($email, FILTER_VALIDATE_EMAIL) === false) {
    fail("이메일 형식이 올바르지 않습니다: $email");
}

$stmt = $pdo->prepare('SELECT id, name, role FROM users WHERE email = ?');
$stmt->execute([$email]);
$user = $stmt->fetch(PDO::FETCH_ASSOC);

if ($user === false) {
    if (!$create) {
        fail("가입되지 않은 이메일입니다: $email\n"
            . "  먼저 /app/ 에서 가입한 뒤 다시 실행하거나, --create 를 붙이세요.");
    }

    $name = trim((string) readline('이름: '));
    if ($name === '') {
        fail('이름이 필요합니다.');
    }
    $password = readSecret('비밀번호(' . MIN_PASSWORD_LENGTH . '자 이상): ');
    if (mb_strlen($password) < MIN_PASSWORD_LENGTH) {
        fail('비밀번호가 너무 짧습니다.');
    }
    if ($password !== readSecret('비밀번호 확인: ')) {
        fail('비밀번호가 일치하지 않습니다.');
    }

    $insert = $pdo->prepare(
        "INSERT INTO users (email, password_hash, name, role, locale)
         VALUES (?, ?, ?, 'admin', 'ko')"
    );
    $insert->execute([$email, password_hash($password, PASSWORD_DEFAULT), $name]);
    $userId = (int) $pdo->lastInsertId();

    $audit->record($userId, AuditRepository::ROLE_CHANGE, 'user', $userId, [
        'to' => 'admin',
        'via' => 'cli:make-admin',
        'created' => true,
    ]);
    fwrite(STDOUT, "관리자 계정을 만들었습니다: #$userId $email\n");
    exit(0);
}

if ($user['role'] === 'admin') {
    fwrite(STDOUT, "이미 관리자입니다: #{$user['id']} $email\n");
    exit(0);
}

$update = $pdo->prepare("UPDATE users SET role = 'admin' WHERE id = ?");
$update->execute([$user['id']]);

$audit->record((int) $user['id'], AuditRepository::ROLE_CHANGE, 'user', (int) $user['id'], [
    'from' => $user['role'],
    'to' => 'admin',
    'via' => 'cli:make-admin',
]);

fwrite(STDOUT, "관리자로 승격했습니다: #{$user['id']} $email ({$user['name']})\n");
