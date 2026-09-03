<?php

declare(strict_types=1);

namespace App\Auth;

/**
 * 유저 등급 판정. (README §17-1)
 *
 * `subscriptions` 테이블은 Step 0 부터 있었지만 **아무도 읽지 않았다.**
 * 등급 차등은 화면이 아니라 여기서 갈린다 — 화면에서만 숨기면 API 를 직접 호출해
 * 우회할 수 있고, 그러면 유료 신호가 공짜로 나간다.
 */
final class Plan
{
    public const FREE = 'free';
    public const BASIC = 'basic';
    public const PRO = 'pro';

    /** 낮은 등급부터. 비교에 쓴다. */
    private const ORDER = [self::FREE => 0, self::BASIC => 1, self::PRO => 2];

    /** FREE 에게 적용할 신호 지연(분). 실시간성이 유료 가치다. */
    public const FREE_DELAY_MINUTES = 15;

    /** 등급별 신호 이력 조회 가능 기간(일). null 은 무제한. */
    public const HISTORY_DAYS = [self::FREE => 0, self::BASIC => 7, self::PRO => null];

    /**
     * 구독 행에서 등급을 뽑는다. 만료·해지는 FREE 로 떨어진다.
     *
     * `status` 만 보면 안 된다 — 해지 처리를 안 했는데 기간이 끝난 구독이 남아 있을 수
     * 있다. `ends_at` 을 함께 봐야 "돈을 안 내고 있는데 PRO" 인 상태가 생기지 않는다.
     *
     * @param array<string, mixed>|null $subscription
     */
    public static function fromSubscription(?array $subscription): string
    {
        if ($subscription === null) {
            return self::FREE;
        }
        if (($subscription['status'] ?? '') !== 'active') {
            return self::FREE;
        }
        $endsAt = $subscription['ends_at'] ?? null;
        if (is_string($endsAt) && strtotime($endsAt) !== false && strtotime($endsAt) < time()) {
            return self::FREE;
        }

        $plan = (string) ($subscription['plan'] ?? self::FREE);

        return isset(self::ORDER[$plan]) ? $plan : self::FREE;
    }

    /** `$plan` 이 `$required` 이상인가. */
    public static function atLeast(string $plan, string $required): bool
    {
        return (self::ORDER[$plan] ?? 0) >= (self::ORDER[$required] ?? 0);
    }

    /** 이 등급이 볼 수 있는 신호의 최소 경과 시간(분). PRO/BASIC 은 0(실시간). */
    public static function delayMinutes(string $plan): int
    {
        return $plan === self::FREE ? self::FREE_DELAY_MINUTES : 0;
    }

    /**
     * 조회 가능한 이력 기간(일). null 이면 무제한, 0 이면 이력 없음.
     *
     * `?? 0` 을 쓰면 안 된다 — PRO 의 값이 null(무제한)이라 널 병합이 0(이력 없음)으로
     * 뒤집어 버린다. 무제한과 없음은 정반대인데 한 글자 차이로 갈린다.
     */
    public static function historyDays(string $plan): ?int
    {
        return array_key_exists($plan, self::HISTORY_DAYS) ? self::HISTORY_DAYS[$plan] : 0;
    }

    /**
     * 등급별로 감출 필드. FREE 는 "왜 그런 신호인가"를 못 본다 — 그게 유료 가치다.
     *
     * @return list<string>
     */
    public static function hiddenFields(string $plan): array
    {
        return match ($plan) {
            self::PRO => [],
            self::BASIC => ['risks'],
            default => [
                'data_sources', 'exchange_consensus_pct', 'risks',
                'tech_score', 'ai_score', 'risk_score',
                'stochastic_k', 'stochastic_d', 'adx_val', 'cci_val',
                'bollinger_position', 'volume_change_pct',
            ],
        };
    }
}
