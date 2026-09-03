<?php

declare(strict_types=1);

namespace App\Tests;

use App\Repository\WebhookRepository;
use PHPUnit\Framework\TestCase;

/**
 * 웹훅 저장소 — 특히 **유저 간 격리**.
 *
 * 웹훅은 유저의 개인 전략과 직결된 정보다(prompt.md v2 §5). 남의 id 를 넣었을 때
 * 조회·폐기·재발급이 모두 "없는 것"으로 떨어지는지가 이 테스트의 핵심이다.
 *
 * SQLite in-memory 를 쓴다. MySQL 과 타입은 다르지만 이 저장소가 쓰는 문법
 * (Prepared Statement / CURRENT_TIMESTAMP / rowCount / 트랜잭션)은 동일하게 동작한다.
 * 실제 MySQL 스키마 대조는 배포 전 수동 검증으로 따로 했다.
 */
final class WebhookRepositoryTest extends TestCase
{
    private \PDO $pdo;
    private WebhookRepository $repo;

    protected function setUp(): void
    {
        $_ENV['APP_URL'] = 'https://example.com';

        $this->pdo = new \PDO('sqlite::memory:');
        $this->pdo->setAttribute(\PDO::ATTR_ERRMODE, \PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec(
            'CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE
            )'
        );
        $this->pdo->exec(
            'CREATE TABLE user_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                webhook_token TEXT NOT NULL UNIQUE,
                label TEXT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_received_at TEXT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                revoked_at TEXT NULL
            )'
        );
        $this->pdo->exec("INSERT INTO users (id, email) VALUES (1, 'a@example.com')");
        $this->pdo->exec("INSERT INTO users (id, email) VALUES (2, 'b@example.com')");

        $this->repo = new WebhookRepository($this->pdo);
    }

    protected function tearDown(): void
    {
        unset($_ENV['APP_URL']);
    }

    public function testCreateIssuesADistinctTokenPerWebhook(): void
    {
        $first  = $this->repo->create(1, '내 전략');
        $second = $this->repo->create(1, null);

        self::assertSame('내 전략', $first['label']);
        self::assertNull($second['label']);
        self::assertNotSame($first['webhook_token'], $second['webhook_token']);
        self::assertSame(1, (int) $first['is_active']);
        self::assertNull($first['revoked_at']);
    }

    public function testUserExists(): void
    {
        self::assertTrue($this->repo->userExists(1));
        self::assertFalse($this->repo->userExists(999));
    }

    // --- 유저 간 격리 -------------------------------------------------------

    public function testAnotherUsersWebhookIsInvisible(): void
    {
        $mine = $this->repo->create(1, '내 것');

        self::assertNotNull($this->repo->find(1, (int) $mine['id']));
        self::assertNull(
            $this->repo->find(2, (int) $mine['id']),
            '유저 B 가 유저 A 의 웹훅을 조회할 수 있으면 안 된다'
        );
    }

    public function testAnotherUserCannotRevoke(): void
    {
        $mine = $this->repo->create(1, '내 것');

        self::assertFalse($this->repo->revoke(2, (int) $mine['id']));
        // 남이 실패한 뒤에도 내 것은 멀쩡해야 한다
        self::assertSame(1, (int) $this->repo->find(1, (int) $mine['id'])['is_active']);
    }

    public function testAnotherUserCannotRotate(): void
    {
        $mine = $this->repo->create(1, '내 것');

        self::assertNull($this->repo->rotate(2, (int) $mine['id']));
        self::assertCount(1, $this->repo->listForUser(1));
        self::assertCount(0, $this->repo->listForUser(2));
    }

    public function testListOnlyReturnsOwnWebhooks(): void
    {
        $this->repo->create(1, 'A-1');
        $this->repo->create(1, 'A-2');
        $this->repo->create(2, 'B-1');

        self::assertCount(2, $this->repo->listForUser(1));
        self::assertCount(1, $this->repo->listForUser(2));
        self::assertSame(['A-2', 'A-1'], array_column($this->repo->listForUser(1), 'label'));
    }

    // --- 폐기·재발급 --------------------------------------------------------

    public function testRevokeHidesItFromTheDefaultListButKeepsTheRow(): void
    {
        $webhook = $this->repo->create(1, '폐기 대상');

        self::assertTrue($this->repo->revoke(1, (int) $webhook['id']));
        self::assertCount(0, $this->repo->listForUser(1));
        self::assertCount(1, $this->repo->listForUser(1, includeRevoked: true));

        // 행을 지우면 external_signals 가 가리키는 수신 이력의 출처가 사라진다.
        $stored = $this->repo->find(1, (int) $webhook['id']);
        self::assertNotNull($stored);
        self::assertNotNull($stored['revoked_at']);
        self::assertSame(0, (int) $stored['is_active']);
    }

    public function testRevokeIsNotRepeatable(): void
    {
        $webhook = $this->repo->create(1, null);

        self::assertTrue($this->repo->revoke(1, (int) $webhook['id']));
        self::assertFalse($this->repo->revoke(1, (int) $webhook['id']));
    }

    public function testRotateMakesANewRowAndRevokesTheOld(): void
    {
        $old = $this->repo->create(1, '5분봉 돌파');

        $new = $this->repo->rotate(1, (int) $old['id']);

        self::assertNotNull($new);
        self::assertNotSame($old['id'], $new['id'], '같은 행의 토큰만 갈아끼우면 안 된다');
        self::assertNotSame($old['webhook_token'], $new['webhook_token']);
        self::assertSame('5분봉 돌파', $new['label'], '라벨은 이어받는다');

        $revoked = $this->repo->find(1, (int) $old['id']);
        self::assertSame(0, (int) $revoked['is_active']);
        self::assertNotNull($revoked['revoked_at']);

        // 살아 있는 것은 새 것 하나뿐
        $active = $this->repo->listForUser(1);
        self::assertCount(1, $active);
        self::assertSame($new['id'], $active[0]['id']);
    }

    public function testRotateOnAMissingWebhookReturnsNull(): void
    {
        self::assertNull($this->repo->rotate(1, 12345));
    }
}
