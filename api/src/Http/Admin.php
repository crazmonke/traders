<?php

declare(strict_types=1);

namespace App\Http;

use App\Auth\Guard;
use App\Auth\Plan;
use App\Repository\AccuracyRepository;
use App\Repository\AdminRepository;
use App\Repository\AuditRepository;
use App\Utils\RedisClient;

/**
 * 관리자 전용 API. (Step 9-b)
 *
 *   GET   /api/v1/admin/users            유저 목록 + 등급
 *   PATCH /api/v1/admin/users/{id}       등급 부여 (결제 전까지는 수동 부여)
 *   GET   /api/v1/admin/ai-budget        AI 호출 예산 모드
 *   PATCH /api/v1/admin/ai-budget        모드 전환 (재시작 없이 즉시 반영)
 *   GET   /api/v1/admin/system           엔진 상태
 *   GET   /api/v1/admin/accuracy         적중률 (Step 7 결과) — 관리자 전용
 *
 * **역할 변경(user↔admin)은 여기 없다.** 승격 API 가 존재하는 것 자체가 권한 상승
 * 취약점이라, CLI(`api/bin/make-admin.php`)로만 한다.
 *
 * 관리자가 아니면 403 이 아니라 **404** 다 — 403 은 "그 경로는 존재한다"를 알려준다.
 * 자동매매는 고객에게 제공하는 기능이 아니므로(README §17), 그 경로의 존재 자체가
 * 새어나가면 안 된다.
 */
final class Admin
{
    private const AI_MODES = ['off', 'seed', 'full'];

    public function __construct(
        private ?AdminRepository $repository = null,
        private ?Guard $guard = null,
        private ?AuditRepository $audit = null,
        private ?AccuracyRepository $accuracy = null,
    ) {
    }

    private function repo(): AdminRepository
    {
        return $this->repository ??= new AdminRepository();
    }

    private function audit(): AuditRepository
    {
        return $this->audit ??= new AuditRepository();
    }

    private function accuracy(): AccuracyRepository
    {
        return $this->accuracy ??= new AccuracyRepository();
    }

    public function handle(string $method, string $subPath): never
    {
        try {
            $admin = ($this->guard ??= new Guard())->admin();
            $this->route($method, $subPath, (int) $admin['id']);
        } catch (\Throwable $exception) {
            if ($exception instanceof \Error || $exception instanceof \PDOException) {
                error_log('admin api 오류: ' . $exception);
                Response::error(Response::SERVER_ERROR, '요청을 처리하지 못했습니다.', 500);
            }
            throw $exception;
        }
    }

    private function route(string $method, string $subPath, int $adminId): never
    {
        if ($subPath === '/users' && $method === 'GET') {
            Response::success(['users' => $this->repo()->users()]);
        }
        if (preg_match('#^/users/(\d+)$#', $subPath, $m) === 1 && $method === 'PATCH') {
            $this->setPlan($adminId, (int) $m[1]);
        }
        if ($subPath === '/ai-budget') {
            $method === 'GET' ? $this->aiBudget() : $this->setAiBudget($adminId, $method);
        }
        if ($subPath === '/system' && $method === 'GET') {
            $this->system();
        }
        if ($subPath === '/accuracy' && $method === 'GET') {
            $this->accuracyReport();
        }

        Response::error(Response::NOT_FOUND, '없는 경로입니다.', 404);
    }

    /**
     * 적중률 (Step 7 결과). **관리자 전용이고 유저에게 노출하지 않는다** —
     * 표현 문구가 법률 검토 대상이다(`docs/LEGAL.md`). 지금은 운영자가 스스로
     * 도구를 신뢰할 수 있는지 판단하기 위한 내부 화면이다.
     *
     * 배점표 버전을 반드시 나눠서 본다. 섞으면 "고쳤더니 나아졌는가"를 알 수 없다.
     */
    private function accuracyReport(): never
    {
        $days = AccuracyRepository::clampDays($_GET['days'] ?? 30);
        $versions = $this->accuracy()->versions($days);

        // 기본값은 결과가 있는 가장 최신 버전이다. 없는 버전을 넘기면 빈 표가 되므로
        // 요청 값이 목록에 없으면 조용히 무시하고 기본값을 쓴다.
        $requested = is_string($_GET['version'] ?? null) ? $_GET['version'] : null;
        $version = in_array($requested, $versions, true) ? $requested : ($versions[0] ?? null);

        Response::success(['accuracy' => [
            'days'            => $days,
            'versions'        => $versions,
            'version'         => $version,
            'small_sample'    => AccuracyRepository::SMALL_SAMPLE,
            'backlog'         => $this->accuracy()->backlog(),
            'by_horizon'      => $this->accuracy()->byVersionHorizon($days),
            'by_symbol'       => $version ? $this->accuracy()->bySymbol($version, $days) : [],
            'by_signal_type'  => $version ? $this->accuracy()->bySignalType($version, $days) : [],
            'by_score_bucket' => $version ? $this->accuracy()->byScoreBucket($version, $days) : [],
            'recent'          => $version ? $this->accuracy()->recent($version, $days) : [],
        ]]);
    }

    /** 등급 수동 부여. 결제(Step 13) 전까지는 이것이 유일한 경로다. */
    private function setPlan(int $adminId, int $userId): never
    {
        $body = Response::jsonBody();
        $plan = $body['plan'] ?? null;
        if (!is_string($plan) || !in_array($plan, [Plan::FREE, Plan::BASIC, Plan::PRO], true)) {
            Response::error(Response::INVALID_REQUEST, 'plan 은 free/basic/pro 입니다.', 400);
        }
        $days = (int) ($body['days'] ?? 30);
        if ($days < 1 || $days > 3650) {
            Response::error(Response::INVALID_REQUEST, 'days 는 1~3650 입니다.', 400);
        }

        if (!$this->repo()->setPlan($userId, $plan, $days)) {
            Response::error(Response::NOT_FOUND, '유저를 찾을 수 없습니다.', 404);
        }

        // 누가 누구에게 무슨 등급을 줬는지 남긴다. 유료 권한이라 기록이 없으면 안 된다.
        $this->audit()->record($adminId, 'PLAN_GRANT', 'user', $userId, [
            'plan' => $plan,
            'days' => $days,
        ]);

        Response::success(['user_id' => $userId, 'plan' => $plan, 'days' => $days]);
    }

    private function aiBudget(): never
    {
        Response::success(['ai_budget' => $this->readAiBudget()]);
    }

    /** @return array<string, mixed> */
    private function readAiBudget(): array
    {
        $override = null;
        $seedKeys = 0;
        try {
            $redis = RedisClient::get();
            $value = $redis->get('settings:ai_mode');
            $override = is_string($value) && $value !== '' ? $value : null;
            // 지금 몇 개 심볼이 seed 슬롯을 쓰고 있는지 = 최근에 실제로 호출이 나갔는지
            $seedKeys = count($redis->keys('ai:seed:*'));
        } catch (\Throwable $exception) {
            error_log('AI 예산 조회 실패: ' . $exception->getMessage());
        }

        return [
            // 엔진의 `.env` 기본값은 API 가 알 수 없다. 재정의가 없으면 "엔진 기본값"이다.
            'mode' => $override,
            'effective' => $override ?? 'engine-default',
            'modes' => self::AI_MODES,
            'seed_slots_in_use' => $seedKeys,
        ];
    }

    private function setAiBudget(int $adminId, string $method): never
    {
        if ($method !== 'PATCH') {
            Response::error(Response::METHOD_NOT_ALLOWED, 'GET 또는 PATCH 만 지원합니다.', 405);
        }
        $mode = Response::jsonBody()['mode'] ?? null;
        if (!is_string($mode) || !in_array($mode, self::AI_MODES, true)) {
            Response::error(
                Response::INVALID_REQUEST,
                'mode 는 ' . implode(' / ', self::AI_MODES) . ' 입니다.',
                400
            );
        }

        try {
            // TTL 을 두지 않는다. 관리자가 끈 것은 다시 켤 때까지 유지돼야 한다.
            RedisClient::get()->set('settings:ai_mode', $mode);
        } catch (\Throwable $exception) {
            // 껐다고 응답했는데 실제로는 안 꺼졌으면 돈이 계속 나간다. 실패를 알려야 한다.
            error_log('AI 예산 전환 실패: ' . $exception->getMessage());
            Response::error(Response::SERVER_ERROR, '설정을 저장하지 못했습니다.', 500);
        }

        $this->audit()->record($adminId, 'AI_BUDGET_CHANGE', 'settings', null, ['mode' => $mode]);

        Response::success(['ai_budget' => $this->readAiBudget()]);
    }

    /** 엔진이 살아 있는지. 시세 캐시에 TTL 이 걸려 있어 키의 존재가 곧 생존 신호다. */
    private function system(): never
    {
        $symbols = [];
        $engineAlive = false;
        try {
            $redis = RedisClient::get();
            foreach ($redis->keys('global:*:price') as $key) {
                $raw = $redis->get($key);
                $data = is_string($raw) ? json_decode($raw, true) : null;
                if (is_array($data)) {
                    $engineAlive = true;
                    $symbols[] = [
                        'symbol' => $data['symbol'] ?? null,
                        'sources' => $data['sources'] ?? [],
                        'updated_at' => $data['updated_at'] ?? null,
                    ];
                }
            }
        } catch (\Throwable $exception) {
            error_log('시스템 상태 조회 실패: ' . $exception->getMessage());
        }

        Response::success([
            'system' => [
                'engine_alive' => $engineAlive,
                'symbols' => $symbols,
                'counts' => $this->repo()->counts(),
            ],
        ]);
    }
}
