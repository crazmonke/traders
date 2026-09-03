<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Utils\Database;
use App\Utils\RedisClient;

/**
 * 백테스트 실행 요청과 결과 조회. (Step 6 DoD)
 *
 *   POST /api/v1/backtest/run      → 작업 큐에 넣고 202
 *   GET  /api/v1/backtest/logs     → 저장된 결과 (reference_exchange 별)
 *
 * **동기로 실행하지 않는다.** 실측으로 한 번에 16초~수 분이 걸린다(1시간봉 2000봉 재생).
 * HTTP 요청을 붙잡고 있으면 타임아웃이 나고, 재시도가 같은 백테스트를 중복 실행한다.
 * PHP 는 큐에 넣기만 하고 Python 엔진의 워커가 꺼내 돌린다.
 *
 * **결과는 `reference_exchange` 로 갈라서만 조회된다.** 신호검증용과 업비트 실전용을
 * 한 목록에 섞으면 화면에서 Step 4 요구사항 4 를 위반하게 된다.
 */
final class Backtest
{
    public const QUEUE_KEY = 'backtest:queue';

    private const REFERENCES = ['GLOBAL_CONSENSUS', 'upbit'];
    private const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d'];
    private const MAX_DAYS = 365;
    private const DEFAULT_DAYS = 90;

    public function __construct(private ?Guard $guard = null)
    {
    }

    public function handle(string $method, string $subPath): never
    {
        $userId = ($this->guard ??= new Guard())->userId();

        if ($subPath === '/run' && $method === 'POST') {
            $this->enqueue($userId);
        }
        if ($subPath === '/logs' && $method === 'GET') {
            $this->logs();
        }

        Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
    }

    private function enqueue(int $userId): never
    {
        $body = Response::jsonBody();

        $job = [
            'job_id' => bin2hex(random_bytes(16)),
            'user_id' => $userId,
            'symbol' => $this->readSymbol($body),
            'reference_exchange' => $this->readChoice(
                $body, 'reference_exchange', self::REFERENCES, 'GLOBAL_CONSENSUS'
            ),
            'timeframe' => $this->readChoice($body, 'timeframe', self::TIMEFRAMES, '1h'),
            'days' => $this->readDays($body),
            'requested_at' => time(),
        ];

        try {
            RedisClient::get()->rpush(self::QUEUE_KEY, [json_encode($job, JSON_UNESCAPED_UNICODE)]);
        } catch (\Throwable $exception) {
            error_log('백테스트 큐 적재 실패: ' . $exception);
            Response::error(Response::SERVER_ERROR, '요청을 접수하지 못했습니다.', 500);
        }

        // 202 — 접수했고 아직 안 끝났다. 결과는 /backtest/logs 로 확인한다.
        Response::success(['job' => $job, 'status' => 'queued'], 202);
    }

    private function logs(): never
    {
        $reference = $this->readChoice($_GET, 'reference_exchange', self::REFERENCES, null);
        if ($reference === null) {
            Response::error(
                Response::INVALID_REQUEST,
                'reference_exchange 가 필요합니다 (신호검증용과 실전용을 섞어 볼 수 없습니다).',
                400
            );
        }

        $sql = 'SELECT id, symbol, reference_exchange, strategy_name, start_date, end_date,
                       total_return_pct, win_rate, avg_profit_loss_ratio, mdd, total_trades,
                       params_json, created_at
                  FROM backtest_logs WHERE reference_exchange = ?';
        $params = [$reference];
        if (($symbol = trim((string) ($_GET['symbol'] ?? ''))) !== '') {
            $sql .= ' AND symbol = ?';
            $params[] = strtoupper($symbol);
        }
        $sql .= ' ORDER BY id DESC LIMIT 50';

        $stmt = Database::getConnection()->prepare($sql);
        $stmt->execute($params);
        $rows = array_map(function (array $row): array {
            $row['params'] = json_decode((string) $row['params_json'], true);
            unset($row['params_json']);
            return $row;
        }, $stmt->fetchAll(\PDO::FETCH_ASSOC));

        Response::success(['reference_exchange' => $reference, 'backtests' => $rows]);
    }

    /** @param array<string, mixed> $source */
    private function readChoice(array $source, string $key, array $allowed, ?string $default): ?string
    {
        $value = $source[$key] ?? null;
        if ($value === null || $value === '') {
            return $default;
        }
        if (!is_string($value) || !in_array($value, $allowed, true)) {
            Response::error(
                Response::INVALID_REQUEST,
                "$key 는 다음 중 하나여야 합니다: " . implode(', ', $allowed),
                400
            );
        }

        return $value;
    }

    /** @param array<string, mixed> $body */
    private function readSymbol(array $body): string
    {
        $symbol = $body['symbol'] ?? '';
        if (!is_string($symbol) || preg_match('/^[A-Za-z0-9]{1,20}$/', $symbol) !== 1) {
            Response::error(Response::INVALID_REQUEST, 'symbol 이 필요합니다.', 400);
        }

        return strtoupper($symbol);
    }

    /** @param array<string, mixed> $body */
    private function readDays(array $body): int
    {
        $days = (int) ($body['days'] ?? self::DEFAULT_DAYS);
        if ($days < 1 || $days > self::MAX_DAYS) {
            Response::error(
                Response::INVALID_REQUEST,
                'days 는 1~' . self::MAX_DAYS . ' 사이여야 합니다.',
                400
            );
        }

        return $days;
    }
}
