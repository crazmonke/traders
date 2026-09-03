<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Auth\Plan;
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
        $guard = $this->guard ??= new Guard();
        $guard->user();
        $plan = $guard->plan();

        $symbol = $this->readSymbol();
        $limit = $this->readLimit();
        $beforeId = $this->readBeforeId();

        // 등급 차등은 **여기서** 걸린다. 화면에서만 숨기면 API 직접 호출로 우회된다.
        $delay = Plan::delayMinutes($plan);
        $historyDays = Plan::historyDays($plan);

        $rows = match ($subPath) {
            '/latest' => $this->repo()->latest($symbol, $limit, $beforeId, $delay, $historyDays),
            '/strong' => $this->repo()->strong($symbol, $limit, $beforeId, $delay, $historyDays),
            default   => $this->one($subPath, $plan),
        };

        $rows = array_map(fn (array $row) => $this->mask($row, $plan), $rows);

        Response::success([
            'plan' => $plan,
            'signals' => $rows,
            // 다음 페이지 커서. 결과가 limit 보다 적으면 더 없다는 뜻이라 null 이다.
            'next_before_id' => count($rows) < $limit ? null : (int) end($rows)['id'],
        ]);
    }

    /** @return list<array<string, mixed>> */
    private function one(string $subPath, string $plan): array
    {
        if (preg_match('#^/(\d+)$#', $subPath, $matches) !== 1) {
            Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
        }
        $signal = $this->repo()->find(
            (int) $matches[1], Plan::delayMinutes($plan), Plan::historyDays($plan)
        );
        if ($signal === null) {
            // 등급 때문에 못 보는 것과 없는 것을 구분하지 않는다. 구분하면
            // "돈 내면 보이는 신호가 존재한다"는 사실이 id 를 훑어 확인된다.
            Response::error(Response::NOT_FOUND, '신호를 찾을 수 없습니다.', 404);
        }

        Response::success(['plan' => $plan, 'signal' => $this->mask($signal, $plan)]);
    }

    /**
     * 등급이 못 보는 필드를 지운다.
     *
     * null 로 채우지 않고 **키 자체를 없앤다** — null 이면 "값이 아직 없다"로 읽히지만,
     * 없으면 "이 등급에는 제공되지 않는다"가 된다. 화면이 잠금 표시를 띄울 근거가 된다.
     *
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function mask(array $row, string $plan): array
    {
        foreach (Plan::hiddenFields($plan) as $field) {
            unset($row[$field]);
        }
        // FREE 는 AI 설명을 한 줄만 본다 (README §17-1).
        if ($plan === Plan::FREE && isset($row['reasons']) && is_array($row['reasons'])) {
            $row['reasons'] = array_slice($row['reasons'], 0, 1);
        }

        return $row;
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
