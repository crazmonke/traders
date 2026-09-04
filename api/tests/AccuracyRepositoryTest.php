<?php

declare(strict_types=1);

namespace App\Tests;

use App\Repository\AccuracyRepository;
use PHPUnit\Framework\TestCase;

/**
 * 적중률 조회 — **핵심은 "서로 다른 배점표의 신호가 섞이지 않는가"** 다.
 *
 * 섞이면 "배점표를 고쳤더니 나아졌는가"를 영원히 알 수 없고, 그 숫자를 근거로
 * 판단한 모든 결정이 흔들린다.
 *
 * SQLite in-memory 를 쓴다. 기간 조건만 드라이버별로 갈라지므로(`since()`)
 * 그 분기 자체도 여기서 실행된다.
 */
final class AccuracyRepositoryTest extends TestCase
{
    private \PDO $pdo;
    private AccuracyRepository $repo;

    protected function setUp(): void
    {
        $this->pdo = new \PDO('sqlite::memory:');
        $this->pdo->setAttribute(\PDO::ATTR_ERRMODE, \PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec(
            "CREATE TABLE ai_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                scoring_version TEXT NOT NULL DEFAULT 'v3',
                signal_type TEXT NOT NULL,
                tech_score INTEGER NOT NULL DEFAULT 50,
                final_score INTEGER NOT NULL DEFAULT 50,
                exchange_consensus_pct REAL NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"
        );
        $this->pdo->exec(
            'CREATE TABLE ai_signal_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                horizon TEXT NOT NULL,
                price_entry REAL NOT NULL,
                price_after REAL,
                return_pct REAL,
                exit_reason TEXT,
                is_accurate INTEGER,
                evaluated_at TEXT
            )'
        );
        $this->repo = new AccuracyRepository($this->pdo);
    }

    private function signal(string $version, string $type = 'BUY', int $score = 80, string $symbol = 'BTC'): int
    {
        $stmt = $this->pdo->prepare(
            'INSERT INTO ai_signals (symbol, scoring_version, signal_type, final_score)
             VALUES (?, ?, ?, ?)'
        );
        $stmt->execute([$symbol, $version, $type, $score]);

        return (int) $this->pdo->lastInsertId();
    }

    private function evaluated(int $signalId, string $horizon, bool $accurate, string $reason = 'TIME_LIMIT', float $ret = 1.0): void
    {
        $stmt = $this->pdo->prepare(
            "INSERT INTO ai_signal_results
                (signal_id, horizon, price_entry, price_after, return_pct, exit_reason,
                 is_accurate, evaluated_at)
             VALUES (?, ?, 100, 101, ?, ?, ?, CURRENT_TIMESTAMP)"
        );
        $stmt->execute([$signalId, $horizon, $ret, $reason, $accurate ? 1 : 0]);
    }

    // --- 버전이 섞이지 않는다 (이 파일의 핵심) --------------------------------

    public function testVersionsAreNeverMixedInOneRow(): void
    {
        $old = $this->signal('v2');
        $new = $this->signal('v3');
        $this->evaluated($old, '1h', false);
        $this->evaluated($new, '1h', true);

        $rows = $this->repo->byVersionHorizon(30);

        $this->assertCount(2, $rows, '같은 horizon 이라도 배점표가 다르면 다른 줄이어야 한다');
        $byVersion = array_column($rows, null, 'version');
        $this->assertSame(0.0, $byVersion['v2']['accuracy_pct']);
        $this->assertSame(100.0, $byVersion['v3']['accuracy_pct']);
    }

    public function testBreakdownsAreScopedToOneVersion(): void
    {
        $this->evaluated($this->signal('v2', 'BUY', 80, 'BTC'), '1h', true);
        $this->evaluated($this->signal('v3', 'BUY', 80, 'ETH'), '1h', true);

        $symbols = array_column($this->repo->bySymbol('v3', 30), 'symbol');

        $this->assertSame(['ETH'], $symbols, 'v3 을 보는데 v2 신호가 들어오면 안 된다');
    }

    public function testNewestVersionComesFirst(): void
    {
        $this->evaluated($this->signal('v2'), '1h', true);
        $this->evaluated($this->signal('v3'), '1h', true);

        $this->assertSame(['v3', 'v2'], $this->repo->versions(30));
    }

    // --- 승률 정의 -----------------------------------------------------------

    public function testUnevaluatedRowsAreNotInTheDenominator(): void
    {
        $id = $this->signal('v3');
        $this->evaluated($id, '1h', true);
        // 아직 평가되지 않은 행 (is_accurate NULL)
        $this->pdo->exec(
            "INSERT INTO ai_signal_results (signal_id, horizon, price_entry) VALUES ($id, '4h', 100)"
        );

        $rows = $this->repo->byVersionHorizon(30);

        $this->assertCount(1, $rows, '미평가 행이 승률 분모에 들어가면 안 된다');
        $this->assertSame(1, $rows[0]['evaluated']);
        $this->assertSame(100.0, $rows[0]['accuracy_pct']);
    }

    public function testExitReasonsAreCounted(): void
    {
        $id = $this->signal('v3');
        $this->evaluated($id, '1h', true, 'TAKE_PROFIT', 5.0);
        $this->evaluated($id, '4h', false, 'STOP_LOSS', -2.5);
        $this->evaluated($id, '1d', true, 'TIME_LIMIT', 0.4);

        $totals = array_column($this->repo->byVersionHorizon(30), null, 'horizon');

        $this->assertSame(1, $totals['1h']['take_profit']);
        $this->assertSame(1, $totals['4h']['stop_loss']);
        $this->assertSame(1, $totals['1d']['time_limit']);
    }

    public function testHorizonsAreOrderedShortestFirst(): void
    {
        $id = $this->signal('v3');
        foreach (['1d', '5m', '4h', '15m', '1h'] as $horizon) {
            $this->evaluated($id, $horizon, true);
        }

        $this->assertSame(
            AccuracyRepository::HORIZONS,
            array_column($this->repo->byVersionHorizon(30), 'horizon')
        );
    }

    // --- 작은 표본 표시 ------------------------------------------------------

    public function testSmallSamplesAreFlagged(): void
    {
        $id = $this->signal('v3');
        $this->evaluated($id, '1h', true);

        $this->assertTrue($this->repo->byVersionHorizon(30)[0]['small_sample']);
    }

    // --- 점수 구간 (점수가 품질을 가르는가) ------------------------------------

    public function testScoreBucketsGroupByTens(): void
    {
        $this->evaluated($this->signal('v3', 'BUY', 72), '1h', true);
        $this->evaluated($this->signal('v3', 'BUY', 78), '1h', false);
        $this->evaluated($this->signal('v3', 'BUY', 85), '1h', true);

        $buckets = array_column($this->repo->byScoreBucket('v3', 30), null, 'bucket');

        $this->assertSame(2, $buckets[70]['evaluated']);
        $this->assertSame(50.0, $buckets[70]['accuracy_pct']);
        $this->assertSame(1, $buckets[80]['evaluated']);
    }

    // --- 기간 조건 -----------------------------------------------------------

    public function testDaysAreClampedToASaneRange(): void
    {
        $this->assertSame(30, AccuracyRepository::clampDays(null));
        $this->assertSame(30, AccuracyRepository::clampDays('abc'));
        $this->assertSame(1, AccuracyRepository::clampDays(0));
        $this->assertSame(1, AccuracyRepository::clampDays(-5));
        $this->assertSame(3650, AccuracyRepository::clampDays(999999));
        $this->assertSame(7, AccuracyRepository::clampDays('7'));
    }

    public function testOlderSignalsFallOutsideTheWindow(): void
    {
        $id = $this->signal('v3');
        $this->pdo->exec("UPDATE ai_signals SET created_at = datetime('now', '-40 days') WHERE id = $id");
        $this->evaluated($id, '1h', true);

        $this->assertSame([], $this->repo->byVersionHorizon(30));
        $this->assertNotSame([], $this->repo->byVersionHorizon(60));
    }

    // --- 잔여 건수 -----------------------------------------------------------

    public function testBacklogExpectsFiveHorizonsPerSignal(): void
    {
        $id = $this->signal('v3');
        $this->signal('v3', 'HOLD');   // HOLD 는 평가 대상이 아니다
        $this->evaluated($id, '1h', true);

        $backlog = $this->repo->backlog();

        $this->assertSame(1, $backlog['signals']);
        $this->assertSame(5, $backlog['expected']);
        $this->assertSame(1, $backlog['evaluated']);
    }
}
