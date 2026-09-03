<?php

declare(strict_types=1);

namespace App\Repository;

use App\Utils\Database;

/**
 * `ai_signals` 조회. (Step 6 DoD)
 *
 * `exchange_consensus_pct` 와 `data_sources_json` 을 반드시 함께 내려준다 —
 * "몇 개 거래소가 같은 신호를 보이는가"가 이 서비스의 차별점이고(README §4),
 * 화면에서 그것을 못 보여주면 다중 거래소로 확장한 의미가 사라진다.
 */
final class SignalRepository
{
    public const MAX_LIMIT = 100;
    public const DEFAULT_LIMIT = 20;

    /** `GET /signals/strong` 이 강한 신호로 치는 기준. */
    public const STRONG_TYPES = ['STRONG_BUY', 'STRONG_SELL'];

    private const COLUMNS =
        'id, symbol, timeframe, signal_type, tech_score, ai_score, risk_score, final_score,
         up_prob, sideways_prob, down_prob, entry_price_global, entry_price_upbit,
         rsi_val, macd_val, bollinger_position, stochastic_k, stochastic_d, adx_val, cci_val,
         volume_change_pct, exchange_consensus_pct, data_sources_json,
         reasons_json, risks_json, created_at';

    public function __construct(private ?\PDO $pdo = null)
    {
    }

    private function db(): \PDO
    {
        return $this->pdo ??= Database::getConnection();
    }

    /**
     * 최신 신호. 커서 페이지네이션.
     *
     * OFFSET 을 쓰지 않는다. 신호는 계속 쌓이므로 페이지를 넘기는 사이에 새 행이 들어오면
     * OFFSET 기반은 같은 행을 두 번 보여주거나 건너뛴다. id 기준 커서가 그 문제가 없다.
     */
    public function latest(?string $symbol, int $limit, ?int $beforeId): array
    {
        $sql = 'SELECT ' . self::COLUMNS . ' FROM ai_signals WHERE 1 = 1';
        $params = [];
        if ($symbol !== null) {
            $sql .= ' AND symbol = ?';
            $params[] = $symbol;
        }
        if ($beforeId !== null) {
            $sql .= ' AND id < ?';
            $params[] = $beforeId;
        }
        $sql .= ' ORDER BY id DESC LIMIT ?';
        $params[] = $limit;

        return $this->fetch($sql, $params);
    }

    /** 강한 신호만. HOLD 와 일반 BUY/SELL 은 제외한다. */
    public function strong(?string $symbol, int $limit, ?int $beforeId): array
    {
        $placeholders = implode(', ', array_fill(0, count(self::STRONG_TYPES), '?'));
        $sql = 'SELECT ' . self::COLUMNS
            . " FROM ai_signals WHERE signal_type IN ($placeholders)";
        $params = self::STRONG_TYPES;
        if ($symbol !== null) {
            $sql .= ' AND symbol = ?';
            $params[] = $symbol;
        }
        if ($beforeId !== null) {
            $sql .= ' AND id < ?';
            $params[] = $beforeId;
        }
        $sql .= ' ORDER BY id DESC LIMIT ?';
        $params[] = $limit;

        return $this->fetch($sql, $params);
    }

    public function find(int $id): ?array
    {
        $rows = $this->fetch('SELECT ' . self::COLUMNS . ' FROM ai_signals WHERE id = ?', [$id]);

        return $rows[0] ?? null;
    }

    /** @return list<array<string, mixed>> */
    private function fetch(string $sql, array $params): array
    {
        $stmt = $this->db()->prepare($sql);
        // LIMIT 자리에는 정수 바인딩이 필요하다. 문자열로 들어가면 MySQL 이 문법 오류를 낸다.
        foreach ($params as $index => $value) {
            $stmt->bindValue(
                $index + 1,
                $value,
                is_int($value) ? \PDO::PARAM_INT : \PDO::PARAM_STR
            );
        }
        $stmt->execute();

        return array_map([$this, 'present'], $stmt->fetchAll(\PDO::FETCH_ASSOC));
    }

    /** JSON 컬럼을 문자열이 아니라 구조로 내려준다. 클라이언트가 두 번 파싱하지 않게. */
    private function present(array $row): array
    {
        foreach (['data_sources_json' => 'data_sources', 'reasons_json' => 'reasons', 'risks_json' => 'risks'] as $column => $key) {
            $decoded = json_decode((string) ($row[$column] ?? ''), true);
            $row[$key] = $decoded === null ? null : $decoded;
            unset($row[$column]);
        }
        foreach (['id', 'tech_score', 'ai_score', 'risk_score', 'final_score'] as $column) {
            if ($row[$column] !== null) {
                $row[$column] = (int) $row[$column];
            }
        }
        foreach (['up_prob', 'sideways_prob', 'down_prob', 'exchange_consensus_pct'] as $column) {
            if ($row[$column] !== null) {
                $row[$column] = (float) $row[$column];
            }
        }

        return $row;
    }
}
