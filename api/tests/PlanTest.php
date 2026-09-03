<?php

declare(strict_types=1);

namespace App\Tests;

use App\Auth\Plan;
use PHPUnit\Framework\TestCase;

/**
 * 등급 판정. 여기가 뚫리면 유료 신호가 공짜로 나간다.
 */
final class PlanTest extends TestCase
{
    public function testNoSubscriptionIsFree(): void
    {
        self::assertSame(Plan::FREE, Plan::fromSubscription(null));
    }

    public function testActiveSubscriptionGivesItsPlan(): void
    {
        self::assertSame(Plan::PRO, Plan::fromSubscription([
            'plan' => 'pro',
            'status' => 'active',
            'ends_at' => date('Y-m-d H:i:s', time() + 86400),
        ]));
    }

    public function testExpiredSubscriptionFallsBackToFree(): void
    {
        /** status 만 보면 "해지 처리 안 했는데 기간이 끝난" 구독이 PRO 로 통과한다. */
        self::assertSame(Plan::FREE, Plan::fromSubscription([
            'plan' => 'pro',
            'status' => 'active',
            'ends_at' => date('Y-m-d H:i:s', time() - 3600),
        ]));
    }

    public function testCanceledSubscriptionIsFree(): void
    {
        self::assertSame(Plan::FREE, Plan::fromSubscription([
            'plan' => 'pro',
            'status' => 'canceled',
            'ends_at' => date('Y-m-d H:i:s', time() + 86400),
        ]));
    }

    public function testUnknownPlanNameIsFree(): void
    {
        /** 모르는 값이 들어오면 가장 낮은 등급으로 떨어진다. */
        self::assertSame(Plan::FREE, Plan::fromSubscription([
            'plan' => 'enterprise',
            'status' => 'active',
            'ends_at' => date('Y-m-d H:i:s', time() + 86400),
        ]));
    }

    public function testPlanOrdering(): void
    {
        self::assertTrue(Plan::atLeast(Plan::PRO, Plan::FREE));
        self::assertTrue(Plan::atLeast(Plan::PRO, Plan::PRO));
        self::assertTrue(Plan::atLeast(Plan::BASIC, Plan::FREE));
        self::assertFalse(Plan::atLeast(Plan::BASIC, Plan::PRO));
        self::assertFalse(Plan::atLeast(Plan::FREE, Plan::BASIC));
    }

    public function testFreeSignalsAreDelayed(): void
    {
        /** FREE 의 상품 정의 - 같은 신호를 늦게 받는다 (2026-09-03 결정). */
        self::assertSame(15, Plan::delayMinutes(Plan::FREE));
        self::assertSame(0, Plan::delayMinutes(Plan::BASIC));
        self::assertSame(0, Plan::delayMinutes(Plan::PRO));
    }

    public function testHistoryWindowByPlan(): void
    {
        self::assertSame(0, Plan::historyDays(Plan::FREE));
        self::assertSame(7, Plan::historyDays(Plan::BASIC));
        self::assertNull(Plan::historyDays(Plan::PRO));
    }

    public function testFreeCannotSeeWhyTheSignalHappened(): void
    {
        /** "왜 그런 신호인가"가 유료 가치다 (FREE 등급 정의). */
        $hidden = Plan::hiddenFields(Plan::FREE);

        self::assertContains('exchange_consensus_pct', $hidden);
        self::assertContains('data_sources', $hidden);
        self::assertContains('risks', $hidden);
        self::assertSame([], Plan::hiddenFields(Plan::PRO));
    }
}
