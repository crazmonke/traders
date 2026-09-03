<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Utils\RedisClient;

/**
 * 시장 요약. (Step 6 DoD — "글로벌 가중 평균가 포함")
 *
 *   GET /api/v1/market/summary
 *
 * DB 가 아니라 **엔진이 써 둔 Redis 캐시**를 읽는다. 이 값들은 초 단위로 갱신되고
 * TTL 이 걸려 있어 DB 에 남기지 않는다(`redis_store` 키 구조 참고).
 *
 * TTL 이 지나 키가 없으면 그 심볼은 `stale: true` 로 내려준다. 낡은 값을 현재가처럼
 * 보여주는 것보다, 엔진이 멈췄다는 사실이 화면에 드러나는 편이 낫다.
 */
final class Market
{
    private const DEFAULT_SYMBOLS = ['BTC', 'ETH', 'XRP', 'SOL', 'DOGE'];
    private const TIMEFRAME = '5m';

    public function __construct(private ?Guard $guard = null)
    {
    }

    public function handle(string $method, string $subPath): never
    {
        if ($method !== 'GET') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'GET 만 지원합니다.', 405);
        }
        if ($subPath !== '/summary') {
            Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
        }
        ($this->guard ??= new Guard())->user();

        $markets = [];
        foreach ($this->symbols() as $symbol) {
            $markets[] = $this->summarize($symbol);
        }

        Response::success(['markets' => $markets]);
    }

    /** @return list<string> */
    private function symbols(): array
    {
        $configured = array_filter(array_map(
            'trim',
            explode(',', (string) ($_ENV['SYMBOLS'] ?? ''))
        ));

        return $configured === [] ? self::DEFAULT_SYMBOLS : array_values($configured);
    }

    private function summarize(string $symbol): array
    {
        $price = RedisClient::json("global:{$symbol}:price");
        $consensus = RedisClient::json("consensus:{$symbol}:" . self::TIMEFRAME);

        return [
            'symbol' => $symbol,
            // 엔진이 살아 있지 않으면 값이 없다. 낡은 값을 지어내지 않는다.
            'stale' => $price === null,
            'price_global_usd' => $price['price'] ?? null,
            'price_upbit_krw' => $price['upbit_price'] ?? null,
            'sources' => $price['sources'] ?? [],
            'source_count' => $price['source_count'] ?? 0,
            'updated_at' => $price['updated_at'] ?? null,
            // 합의·점수는 신호 캐시 쪽에 있다. 없으면 아직 평가 전이다.
            'signal_type' => $consensus['signal_type'] ?? null,
            'final_score' => $consensus['final_score'] ?? null,
            'tech_score' => $consensus['tech_score'] ?? null,
            'exchange_consensus_pct' => $consensus['exchange_consensus_pct'] ?? null,
            'direction' => $consensus['direction'] ?? null,
        ];
    }
}
