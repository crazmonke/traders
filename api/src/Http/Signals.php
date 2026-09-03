<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Repository\SignalRepository;

/**
 * 신호 조회. (Step 6 DoD)
 *
 *   GET /api/v1/signals/latest?symbol=BTC&limit=20&before_id=123
 *   GET /api/v1/signals/strong?...
 *   GET /api/v1/signals/{id}
 *
 * 응답에 `exchange_consensus_pct` 와 `data_sources` 가 항상 들어간다 — 몇 개 거래소가
 * 같은 방향을 보는지가 이 서비스의 차별점이다(README §4).
 */
final class Signals
{
    public function __construct(
        private ?SignalRepository $repository = null,
        private ?Guard $guard = null,
    ) {
    }

    private function repo(): SignalRepository
    {
        return $this->repository ??= new SignalRepository();
    }

    public function handle(string $method, string $subPath): never
    {
        if ($method !== 'GET') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'GET 만 지원합니다.', 405);
        }
        ($this->guard ??= new Guard())->user();

        $symbol = $this->readSymbol();
        $limit = $this->readLimit();
        $beforeId = $this->readBeforeId();

        $rows = match ($subPath) {
            '/latest' => $this->repo()->latest($symbol, $limit, $beforeId),
            '/strong' => $this->repo()->strong($symbol, $limit, $beforeId),
            default   => $this->one($subPath),
        };

        Response::success([
            'signals' => $rows,
            // 다음 페이지 커서. 결과가 limit 보다 적으면 더 없다는 뜻이라 null 이다.
            'next_before_id' => count($rows) < $limit ? null : (int) end($rows)['id'],
        ]);
    }

    /** @return list<array<string, mixed>> */
    private function one(string $subPath): array
    {
        if (preg_match('#^/(\d+)$#', $subPath, $matches) !== 1) {
            Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
        }
        $signal = $this->repo()->find((int) $matches[1]);
        if ($signal === null) {
            Response::error(Response::NOT_FOUND, '신호를 찾을 수 없습니다.', 404);
        }

        Response::success(['signal' => $signal]);
    }

    private function readSymbol(): ?string
    {
        $symbol = trim((string) ($_GET['symbol'] ?? ''));
        if ($symbol === '') {
            return null;
        }
        // 심볼은 영문 대문자·숫자뿐이다. LIKE 를 쓰지 않으므로 주입 위험은 없지만,
        // 형식이 아닌 값으로 DB 를 훑을 이유도 없다.
        if (preg_match('/^[A-Z0-9]{1,20}$/', strtoupper($symbol)) !== 1) {
            Response::error(Response::INVALID_REQUEST, 'symbol 형식이 올바르지 않습니다.', 400);
        }

        return strtoupper($symbol);
    }

    private function readLimit(): int
    {
        $limit = (int) ($_GET['limit'] ?? SignalRepository::DEFAULT_LIMIT);
        if ($limit < 1) {
            $limit = SignalRepository::DEFAULT_LIMIT;
        }

        // 상한을 두지 않으면 한 번의 요청으로 테이블 전체를 끌어갈 수 있다.
        return min($limit, SignalRepository::MAX_LIMIT);
    }

    private function readBeforeId(): ?int
    {
        $before = $_GET['before_id'] ?? null;
        if ($before === null || $before === '') {
            return null;
        }
        $value = (int) $before;

        return $value > 0 ? $value : null;
    }
}
