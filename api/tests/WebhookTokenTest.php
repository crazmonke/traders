<?php

declare(strict_types=1);

namespace App\Tests;

use App\Utils\WebhookToken;
use PHPUnit\Framework\TestCase;

/**
 * 웹훅 토큰 — 이 문자열 하나가 유저를 식별하는 유일한 수단이다.
 */
final class WebhookTokenTest extends TestCase
{
    public function testTokenFitsTheColumnAndIsUrlSafe(): void
    {
        $token = WebhookToken::generate();

        // user_webhooks.webhook_token 이 CHAR(43). 길이가 어긋나면 INSERT 가 잘린다.
        self::assertSame(43, strlen($token));
        self::assertMatchesRegularExpression('/^[A-Za-z0-9_-]+$/', $token);
        self::assertSame($token, rawurlencode($token), 'URL 에 그대로 들어가야 한다');
    }

    public function testTokensDoNotRepeat(): void
    {
        $tokens = [];
        for ($i = 0; $i < 500; $i++) {
            $tokens[] = WebhookToken::generate();
        }

        self::assertCount(500, array_unique($tokens));
    }

    public function testWellFormedRejectsAnythingButOurFormat(): void
    {
        self::assertTrue(WebhookToken::isWellFormed(WebhookToken::generate()));
        self::assertFalse(WebhookToken::isWellFormed(''));
        self::assertFalse(WebhookToken::isWellFormed(str_repeat('a', 42)));
        self::assertFalse(WebhookToken::isWellFormed(str_repeat('a', 44)));
        // base64 표준 문자(+ / =)는 우리 형식이 아니다
        self::assertFalse(WebhookToken::isWellFormed(str_repeat('a', 42) . '+'));
        self::assertFalse(WebhookToken::isWellFormed(str_repeat('a', 42) . '='));
    }

    public function testUrlUsesTheWebhookBaseWhenSet(): void
    {
        $_ENV['WEBHOOK_BASE_URL'] = 'https://api.example.com/';
        $_ENV['APP_URL'] = 'http://localhost:8080';

        self::assertSame(
            'https://api.example.com/webhook/tv/abc',
            WebhookToken::url('abc')
        );
    }

    public function testUrlFallsBackToAppUrl(): void
    {
        unset($_ENV['WEBHOOK_BASE_URL']);
        $_ENV['APP_URL'] = 'https://upsignal.example.com';

        self::assertSame(
            'https://upsignal.example.com/webhook/tv/abc',
            WebhookToken::url('abc')
        );
    }

    protected function tearDown(): void
    {
        unset($_ENV['WEBHOOK_BASE_URL'], $_ENV['APP_URL']);
    }
}
