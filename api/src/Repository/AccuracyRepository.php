<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;

/**
 * 적중률 조회 (Step 7 결과 열람). **관리자 화면 전용.**
 *
 * 유저 노출은 하지 않는다 — 표현 문구가 법률 검토 대상이다(`docs/LEGAL.md`).
 * 지금은 운영자가 스스로 도구를 신뢰할 수 있는지 판단하기 위한 내부 화면이다.
 *
 * ### 배점표 버전을 반드시 나눈다
 *
 * 배점표를 고치면 그 전후 신호는 다른 규칙으로 만들어진 다른 것이다. 섞어서 평균을
 * 내면 "고쳤더니 나아졌는가"를 영원히 알 수 없다. 모든 조회가
 * `ai_signals.scoring_version` 을 기준으로 갈라진다
 * (`trading_engine/strategy/versioning.py`).
 *
 * ### 기간을 DB 시계로 자르는 이유
 *
 * 이 서버는 **PHP 가 UTC, MySQL 이 KST** 로 돈다. 자르는 시각을 PHP 에서 계산해
 * 파라미터로 넘기면 9시간이 어긋나 **가장 최근 신호가 통째로 빠진다.** 그래서 기간
 * 조건만은 DB 쪽 함수로 만든다. 값은 정수로 검증·clamp 한 뒤에 넣는다.
 */
final class AccuracyRepository
{
    /** 짧은 것부터. `ai_signal_results.horizon` ENUM 과 같은 순서다. */
    public const HORIZONS = ['5m', '15m', '1h', '4h', '1d'];

    private const MAX_DAYS = 3650;

    /** 이 건수 아래는 통계로 읽지 말라고 화면이 표시한다. */
    public const SMALL_SAMPLE = 30;

    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    public static function clampDays(mixed $days): int
    {
        $value = is_numeric($days) ? (int) $days : 30;

        return max(1, min($value, self::MAX_DAYS));
    }

    /**
     * 기간 하한을 **DB 시계로** 만든다. PHP 시계(UTC)와 9시간 어긋나기 때문이다.
     * `$days` 는 호출 전에 `clampDays()` 로 정수 확정된다.
     */
    private function since(int $days): string
    {
        $driver = $this->db()->getAttribute(\PDO::ATTR_DRIVER_NAME);

        return $driver === 'sqlite'
            ? "datetime('now', '-" . $days . " days')"
            : 'NOW() - INTERVAL ' . $days . ' DAY';
    }

    /**
     * 점수를 10점 구간으로 내리는 식. **반드시 내림이어야 한다.**
     *
     * MySQL 의 `CAST(x AS SIGNED)` 는 내림이 아니라 반올림이라 79점이 80 구간으로
     * 올라간다. SQLite 에는 `DIV` 가 없고 `FLOOR()` 는 빌드 옵션에 따라 없을 수 있다.
     * 그래서 드라이버별로 확실한 정수 나눗셈을 쓴다.
     */
    private function bucket(): string
    {
        $driver = $this->db()->getAttribute(\PDO::ATTR_DRIVER_NAME);

        return $driver === 'sqlite'
            ? 'CAST(s.final_score / 10 AS INTEGER) * 10'
            : '(s.final_score DIV 10) * 10';
    }

    /**
     * 집계 공통부. 판정이 끝난 행만 센다 — `is_accurate` 가 NULL 인 행은
     * "아직 평가 안 됨"이라 승률 분모에 들어가면 안 된다.
     */
    private function metrics(): string
    {
        return "COUNT(*) AS evaluated,
                SUM(r.is_accurate) AS accurate,
                AVG(r.return_pct) AS avg_return,
                SUM(CASE WHEN r.exit_reason = 'TAKE_PROFIT' THEN 1 ELSE 0 END) AS take_profit,
                SUM(CASE WHEN r.exit_reason = 'STOP_LOSS'   THEN 1 ELSE 0 END) AS stop_loss,
                SUM(CASE WHEN r.exit_reason = 'TIME_LIMIT'  THEN 1 ELSE 0 END) AS time_limit";
    }

    /** @return list<array<string, mixed>> */
    private function rows(string $sql, array $params = []): array
    {
        $stmt = $this->db()->prepare($sql);
        $stmt->execute($params);

        return array_map([$this, 'shape'], $stmt->fetchAll(\PDO::FETCH_ASSOC) ?: []);
    }

    /** 숫자 컬럼의 타입을 고정한다. PDO 는 드라이버마다 문자열로 주기도 한다. */
    private function shape(array $row): array
    {
        foreach (['evaluated', 'accurate', 'take_profit', 'stop_loss', 'time_limit', 'signals'] as $key) {
            if (array_key_exists($key, $row)) {
                $row[$key] = (int) $row[$key];
            }
        }
        foreach (['avg_return', 'avg_score'] as $key) {
            if (array_key_exists($key, $row)) {
                $row[$key] = $row[$key] === null ? null : round((float) $row[$key], 3);
            }
        }
        // 적중률은 여기서 낸다. 화면이 저마다 계산하면 정의가 갈라진다.
        // `accurate` 를 뽑지 않는 조회(버전 목록)도 이 함수를 지나므로 둘 다 확인한다.
        if (isset($row['evaluated'], $row['accurate'])) {
            $row['accuracy_pct'] = $row['evaluated'] > 0
                ? round($row['accurate'] / $row['evaluated'] * 100, 1)
                : null;
            $row['small_sample'] = $row['evaluated'] < self::SMALL_SAMPLE;
        }

        return $row;
    }

    /** 평가 결과가 존재하는 배점표 버전. 최신(사전순 내림차순)이 앞이다. */
    public function versions(int $days): array
    {
        $rows = $this->rows(
            'SELECT s.scoring_version AS version, COUNT(*) AS evaluated
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.created_at >= ' . $this->since($days) . '
              GROUP BY s.scoring_version
              ORDER BY s.scoring_version DESC'
        );

        return array_map(static fn (array $r): string => (string) $r['version'], $rows);
    }

    /** 표제 표 — 배점표 × 시간제한. **여기서 두 버전이 섞이지 않는 것이 핵심이다.** */
    public function byVersionHorizon(int $days): array
    {
        return $this->sortByHorizon($this->rows(
            'SELECT s.scoring_version AS version, r.horizon, ' . $this->metrics() . '
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.created_at >= ' . $this->since($days) . '
              GROUP BY s.scoring_version, r.horizon'
        ));
    }

    public function bySymbol(string $version, int $days): array
    {
        return $this->rows(
            'SELECT s.symbol, ' . $this->metrics() . '
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.scoring_version = ?
                AND s.created_at >= ' . $this->since($days) . '
              GROUP BY s.symbol
              ORDER BY s.symbol',
            [$version]
        );
    }

    public function bySignalType(string $version, int $days): array
    {
        return $this->rows(
            'SELECT s.signal_type, ' . $this->metrics() . '
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.scoring_version = ?
                AND s.created_at >= ' . $this->since($days) . '
              GROUP BY s.signal_type',
            [$version]
        );
    }

    /**
     * 점수 구간별. **"점수가 높을수록 좋은 신호인가"를 보는 표다.**
     *
     * 배점표 재설계(Step 16) 백테스트에서 v2·v3 **둘 다 점수가 품질을 제대로 가르지
     * 못했다.** 운영 데이터에서도 그런지 계속 지켜봐야 하므로 화면에 상설로 둔다.
     */
    public function byScoreBucket(string $version, int $days): array
    {
        return $this->rows(
            'SELECT ' . $this->bucket() . ' AS bucket,
                    AVG(s.final_score) AS avg_score, ' . $this->metrics() . '
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.scoring_version = ?
                AND s.created_at >= ' . $this->since($days) . '
              GROUP BY bucket
              ORDER BY bucket',
            [$version]
        );
    }

    /** 개별 신호 최근 목록. 집계만 보면 무엇이 이상한지 알 수 없다. */
    public function recent(string $version, int $days, int $limit = 50): array
    {
        $limit = max(1, min($limit, 500));

        return $this->rows(
            'SELECT s.id AS signal_id, s.symbol, s.signal_type, s.final_score,
                    s.tech_score, s.exchange_consensus_pct, s.created_at,
                    r.horizon, r.return_pct, r.exit_reason, r.is_accurate,
                    r.price_entry, r.price_after, r.evaluated_at
               FROM ai_signal_results r
               JOIN ai_signals s ON s.id = r.signal_id
              WHERE r.is_accurate IS NOT NULL
                AND s.scoring_version = ?
                AND s.created_at >= ' . $this->since($days) . '
              ORDER BY s.created_at DESC, r.horizon
              LIMIT ' . $limit,
            [$version]
        );
    }

    /** 아직 평가되지 않은 건. 추적기가 도는지 화면에서 바로 보이게 한다. */
    public function backlog(): array
    {
        $stmt = $this->db()->query(
            "SELECT (SELECT COUNT(*) FROM ai_signals WHERE signal_type <> 'HOLD') AS signals,
                    (SELECT COUNT(*) FROM ai_signal_results WHERE is_accurate IS NOT NULL)
                        AS evaluated,
                    (SELECT MAX(evaluated_at) FROM ai_signal_results) AS last_evaluated_at"
        );
        $row = $stmt->fetch(\PDO::FETCH_ASSOC) ?: [];

        return [
            'signals'           => (int) ($row['signals'] ?? 0),
            'evaluated'         => (int) ($row['evaluated'] ?? 0),
            // 신호 하나당 horizon 5개가 나온다. 그래서 기대치는 신호 수 × 5 다.
            'expected'          => (int) ($row['signals'] ?? 0) * count(self::HORIZONS),
            'last_evaluated_at' => $row['last_evaluated_at'] ?? null,
        ];
    }

    /** ENUM 순서(짧은 제한부터)로 정렬한다. `FIELD()` 는 MySQL 전용이라 PHP 에서 한다. */
    private function sortByHorizon(array $rows): array
    {
        $order = array_flip(self::HORIZONS);
        usort($rows, static function (array $a, array $b) use ($order): int {
            $version = strcmp((string) ($b['version'] ?? ''), (string) ($a['version'] ?? ''));

            return $version !== 0
                ? $version
                : ($order[$a['horizon']] ?? 99) <=> ($order[$b['horizon']] ?? 99);
        });

        return $rows;
    }
}
