/*
 * Traders 대시보드.
 *
 * **DB 에 직접 붙지 않는다.** Step 6 의 REST API 만 호출한다(Step 9 DoD).
 * 빌드 도구를 쓰지 않는다 — 이 저장소에 node 툴체인이 없고, 화면 몇 개에 번들러를
 * 들이면 배포(rsync)와 CI 가 통째로 복잡해진다.
 *
 * 등급 차등은 **서버가 이미 걸어서 내려준다.** 여기서는 "없는 필드"를 잠금으로 그릴 뿐이다.
 * 화면에서 숨기는 방식이면 API 를 직접 호출해 우회할 수 있어서, 차단은 서버 몫이다.
 */

const API = '/api/v1';
const TOKEN_KEY = 'traders.token';

const state = {
    token: localStorage.getItem(TOKEN_KEY) || null,
    user: null,
    plan: 'free',
};

/* --- API 클라이언트 ------------------------------------------------------- */

async function call(path, { method = 'GET', body = null, auth = true } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;

    const res = await fetch(API + path, {
        method,
        headers,
        body: body === null ? null : JSON.stringify(body),
    });

    let payload = null;
    try {
        payload = await res.json();
    } catch {
        // 서버가 JSON 이 아닌 것을 돌려준 경우(프록시 오류 페이지 등)
        throw { code: 'SERVER_ERROR', message: '서버 응답을 해석할 수 없습니다.', status: res.status };
    }

    if (!res.ok || payload.success === false) {
        const err = payload.error || {};
        // 토큰이 죽었으면(만료·로그아웃) 붙잡고 있을 이유가 없다.
        if (res.status === 401) logout(false);
        throw { code: err.code || 'ERROR', message: err.message || '요청에 실패했습니다.', status: res.status };
    }

    return payload.data;
}

/* --- 인증 ---------------------------------------------------------------- */

function setSession(data) {
    state.token = data.token;
    state.user = data.user;
    state.plan = data.plan;
    localStorage.setItem(TOKEN_KEY, data.token);
    document.documentElement.dataset.locale = data.user.locale || 'ko';
}

function logout(callServer = true) {
    if (callServer && state.token) {
        // 서버 폐기 목록에 올려야 실제로 무효가 된다. 실패해도 로컬은 지운다.
        call('/auth/logout', { method: 'POST' }).catch(() => {});
    }
    state.token = null;
    state.user = null;
    state.plan = 'free';
    localStorage.removeItem(TOKEN_KEY);
    location.hash = '#/login';
}

/* --- 표시 헬퍼 ------------------------------------------------------------ */

/*
 * 등급 표기. **"매수/매도"를 쓰지 않는다** (docs/LEGAL.md).
 *
 * 유료 + 특정 코인 + 매수/매도 가 합쳐지면 투자 추천 서비스로 평가될 소지가 커진다.
 * 우리가 하는 일은 "사라/팔아라"가 아니라 "시장 데이터를 분석하면 이런 상태다"이므로
 * 표기도 그렇게 맞춘다. DB 의 `signal_type` ENUM 은 내부 값이라 그대로 둔다.
 */
const SIGNAL_LABEL = {
    STRONG_BUY: { cls: 'buy', mark: '▲▲', text: '상승 신호 강함' },
    BUY: { cls: 'buy', mark: '▲', text: '상승 신호' },
    HOLD: { cls: 'hold', mark: '―', text: '중립' },
    SELL: { cls: 'sell', mark: '▼', text: '하락 신호' },
    STRONG_SELL: { cls: 'sell', mark: '▼▼', text: '하락 신호 강함' },
};

/** 신호 배지. **색과 함께 기호를 반드시 넣는다** — 색각 이상에서도 읽혀야 한다. */
function signalBadge(type) {
    const s = SIGNAL_LABEL[type] || SIGNAL_LABEL.HOLD;
    return `<span class="sig ${s.cls}">${s.mark} ${s.text}</span>`;
}

/** 등급 때문에 서버가 내려주지 않은 값. 숨기지 않고 무엇이 필요한지 알려준다. */
function locked(need = 'BASIC') {
    return `<span class="lock">${need} 이상</span>`;
}

/**
 * 아직 값이 없는 상태(엔진이 평가 전, 데이터 미도착).
 *
 * 잠금과 **반드시 구분**한다. 자물쇠로 그리면 "결제하면 보이는 값"으로 읽히는데,
 * 실제로는 돈을 내도 없는 값이다.
 */
function pending(text) {
    return `<span class="pending">${text}</span>`;
}

/** 값이 있으면 포맷하고, 키가 없으면 잠금으로 그린다. */
function fieldOr(row, key, format, need) {
    return key in row && row[key] !== null ? format(row[key]) : locked(need);
}

function num(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return Number(value).toLocaleString('ko-KR', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
}

function price(value) {
    if (value === null || value === undefined) return '—';
    const v = Number(value);
    return v >= 1000 ? v.toLocaleString('ko-KR', { maximumFractionDigits: 0 })
                     : v.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
}

/**
 * "N분 전". API 가 주는 시각은 **DB 서버의 로컬 시각**(KST)이고 시간대 표시가 없다.
 *
 * 예전에는 `+ 'Z'` 를 붙여 UTC 로 읽었는데, 서버가 KST 라 9시간이 어긋나 모든 시각이
 * 미래가 됐고 화면에는 전부 "방금"으로 나왔다. 접미사를 붙이지 않으면 JS 가 브라우저
 * 로컬 시각으로 읽는다 — 지금은 서버·브라우저가 같은 KST 라 이것이 맞다.
 *
 * **다국어(Step 10) 때 반드시 정리할 것**: API 가 시간대까지 담은 ISO8601 로 주고
 * 화면은 그것만 믿게 해야 다른 시간대의 접속자에게도 맞는다.
 */
function when(ts) {
    if (!ts) return '—';
    const d = new Date(ts.replace(' ', 'T'));
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return '방금';
    if (mins < 60) return `${mins}분 전`;
    if (mins < 1440) return `${Math.floor(mins / 60)}시간 전`;
    return d.toLocaleDateString('ko-KR');
}

function esc(text) {
    const el = document.createElement('div');
    el.textContent = text ?? '';
    return el.innerHTML;
}

const LEGAL = `본 서비스에서 제공하는 분석 결과 및 AI 신호는 투자 참고용 데이터이며,
수익을 보장하지 않습니다. 모든 투자의 최종 책임은 본인에게 있습니다.
표시되는 시세는 여러 거래소의 데이터를 자체 집계한 것이며,
트레이딩뷰(TradingView)의 공식 데이터가 아닙니다.`;

/** 확률 옆에 반드시 붙인다. 숫자만 보이면 "보장"으로 읽힌다 (docs/LEGAL.md). */
const MODEL_OUTPUT_NOTE =
    '과거 데이터 기반 모델 출력값이며 실제 미래 수익률을 보장하지 않습니다.';

/** 백테스트 화면 고지. 실현되지 않은 수익률 제시는 자본시장법상 광고 금지 대상이다. */
const BACKTEST_NOTE =
    '과거 데이터로 전략을 재생한 시뮬레이션 결과입니다. 실제 거래 결과가 아니며, '
    + '과거 성과는 미래의 수익을 보장하지 않습니다.';

/* --- 화면 ---------------------------------------------------------------- */

const app = () => document.getElementById('app');

function shell(inner, active) {
    const items = [
        ['#/', '대시보드'],
        ['#/signals', '신호'],
        ['#/backtest', '백테스트'],
    ];
    // 관리자 메뉴는 관리자에게만 그린다. 차단 자체는 서버가 404 로 한다.
    if (state.user?.role === 'admin') {
        items.push(['#/admin', '관리'], ['#/accuracy', '적중률']);
    }

    const nav = items.map(([href, label]) =>
        `<a href="${href}" class="${active === href ? 'on' : ''}">${label}</a>`
    ).join('');

    return `
    <div class="topbar">
        <div class="brand">Traders<span>.</span></div>
        <nav class="nav">${nav}</nav>
        <span class="plan-tag">${esc(state.plan.toUpperCase())}</span>
        <button class="ghost" onclick="window.__logout()">로그아웃</button>
    </div>
    <div class="wrap">${inner}</div>
    <div class="legal">${esc(LEGAL)}</div>`;
}

async function viewLogin() {
    app().innerHTML = `
    <div class="auth">
        <div class="brand" style="font-size:26px;margin-bottom:6px">Traders<span>.</span></div>
        <p class="sub">한 거래소의 착시를 걸러냅니다.<br>5개 거래소가 동시에 같은 방향을 볼 때만 신호를 냅니다.</p>
        <div id="msg"></div>
        <form id="f">
            <div class="field" id="nameField" hidden>
                <label for="name">이름</label><input id="name" autocomplete="name">
            </div>
            <div class="field">
                <label for="email">이메일</label>
                <input id="email" type="email" autocomplete="email" required>
            </div>
            <div class="field">
                <label for="password">비밀번호</label>
                <input id="password" type="password" autocomplete="current-password" required>
            </div>
            <div class="row">
                <button type="submit" id="submit">로그인</button>
                <div class="spacer"></div>
                <button type="button" class="ghost" id="toggle">회원가입</button>
            </div>
        </form>
    </div>
    <div class="legal">${esc(LEGAL)}</div>`;

    let mode = 'login';
    const $ = (id) => document.getElementById(id);

    $('toggle').onclick = () => {
        mode = mode === 'login' ? 'register' : 'login';
        $('nameField').hidden = mode === 'login';
        $('name').required = mode === 'register';
        $('submit').textContent = mode === 'login' ? '로그인' : '가입하기';
        $('toggle').textContent = mode === 'login' ? '회원가입' : '로그인으로';
        $('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
        $('msg').innerHTML = '';
    };

    $('f').onsubmit = async (event) => {
        event.preventDefault();
        $('submit').disabled = true;
        try {
            const body = { email: $('email').value, password: $('password').value };
            if (mode === 'register') body.name = $('name').value;
            setSession(await call(`/auth/${mode}`, { method: 'POST', body, auth: false }));
            location.hash = '#/';
        } catch (error) {
            $('msg').innerHTML = `<div class="msg err">${esc(error.message)}</div>`;
        } finally {
            $('submit').disabled = false;
        }
    };
}

async function viewDashboard() {
    app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/');

    const [market, signals] = await Promise.all([
        call('/market/summary').catch(() => ({ markets: [] })),
        call('/signals/strong?limit=8').catch(() => ({ signals: [] })),
    ]);

    const cards = market.markets.map((m) => {
        const sig = m.signal_type ? signalBadge(m.signal_type) : pending('평가 전');
        const stale = m.stale
            // 엔진이 멈췄을 때 낡은 값을 현재가처럼 보여주지 않는다. 서버가 stale 을 준다.
            ? '<div class="meta" style="color:var(--warn)">⚠ 시세 수신 중단</div>'
            : `<div class="meta">${m.source_count}개 거래소 · ${when(null) || ''}</div>`;
        return `<div class="card">
            <div class="row"><span class="sym">${esc(m.symbol)}</span><div class="spacer"></div>${sig}</div>
            <div class="price">${m.stale ? '—' : '$' + price(m.price_global_usd)}</div>
            ${stale}
        </div>`;
    }).join('');

    app().innerHTML = shell(`
        <h1>대시보드</h1>
        <p class="sub">거래량 가중 평균가 · 거래소 합의 기준</p>
        <div class="grid">${cards || '<div class="empty">시세가 아직 없습니다.</div>'}</div>
        <h2>강한 신호</h2>
        ${signalTable(signals.signals)}`, '#/');
}

function signalTable(rows) {
    if (!rows || rows.length === 0) {
        return '<div class="empty">표시할 신호가 없습니다.</div>';
    }
    const body = rows.map((r) => `
        <tr onclick="location.hash='#/signals/${r.id}'">
            <td><strong>${esc(r.symbol)}</strong></td>
            <td>${signalBadge(r.signal_type)}</td>
            <td class="num">${r.final_score}</td>
            <td class="num">${fieldOr(r, 'exchange_consensus_pct', (v) => num(v, 0) + '%', 'BASIC')}</td>
            <td class="num">${fieldOr(r, 'tech_score', (v) => v, 'BASIC')}</td>
            <td style="color:var(--muted)">${when(r.created_at)}</td>
        </tr>`).join('');

    return `<div class="table-wrap"><table>
        <thead><tr>
            <th>심볼</th><th>신호</th><th class="num">최종점수</th>
            <th class="num">합의율</th><th class="num">기술점수</th><th>시각</th>
        </tr></thead>
        <tbody>${body}</tbody></table></div>`;
}

async function viewSignals() {
    app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/signals');

    const symbol = new URLSearchParams(location.hash.split('?')[1] || '').get('symbol');
    const query = symbol ? `&symbol=${encodeURIComponent(symbol)}` : '';
    const data = await call(`/signals/latest?limit=50${query}`);

    const notice = state.plan === 'free'
        ? `<div class="msg ok">FREE 등급은 신호가 15분 지연되고 근거가 일부 가려집니다.
           실시간과 전체 근거는 BASIC 이상에서 볼 수 있습니다.</div>`
        : '';

    // 심볼을 고르면 그 심볼의 점수 추이를 그린다. 여러 심볼이 섞인 추이는 의미가 없다.
    const symbols = [...new Set(data.signals.map((s) => s.symbol))].sort();
    const chips = symbols.map((s) =>
        `<button class="${symbol === s ? 'on' : ''}" data-symbol="${esc(s)}">${esc(s)}</button>`
    ).join('');

    const chart = symbol
        ? `<h2>${esc(symbol)} 점수 추이</h2><div class="card">${sparkline(
            data.signals.slice().reverse().map((s) => ({
                value: Number(s.final_score), label: when(s.created_at),
            })))}</div>`
        : '';

    app().innerHTML = shell(`
        <h1>신호</h1>
        <p class="sub">최신순 · ${data.signals.length}건</p>
        ${notice}
        <div class="tabs">
            <button class="${symbol ? '' : 'on'}" data-symbol="">전체</button>${chips}
        </div>
        ${chart}
        ${symbol ? '<h2>내역</h2>' : ''}
        ${signalTable(data.signals)}`, '#/signals');

    document.querySelectorAll('.tabs button[data-symbol]').forEach((btn) => {
        btn.onclick = () => {
            location.hash = btn.dataset.symbol ? `#/signals?symbol=${btn.dataset.symbol}` : '#/signals';
            viewSignals();
        };
    });
}

async function viewSignalDetail(id) {
    app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/signals');
    const { signal: s } = await call(`/signals/${id}`);

    const scoreBar = (label, value, need) => {
        if (!(value in s) || s[value] === null) {
            return `<div class="bar-row"><span class="label">${label}</span>${locked(need)}<span></span></div>`;
        }
        const v = Number(s[value]);
        return `<div class="bar-row">
            <span class="label">${label}</span>
            <span class="bar"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></span>
            <span class="val">${num(v, 0)}</span></div>`;
    };

    const reasons = (s.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
    const risks = 'risks' in s
        ? `<ul>${(s.risks || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`
        : locked('BASIC');

    app().innerHTML = shell(`
        <div class="row">
            <h1>${esc(s.symbol)}</h1>${signalBadge(s.signal_type)}
            <div class="spacer"></div>
            <a href="#/signals">← 목록</a>
        </div>
        <p class="sub">${when(s.created_at)} · ${esc(s.timeframe)}</p>

        <div class="card">
            <h2 style="margin-top:0">점수 구성</h2>
            <div class="bars">
                ${scoreBar('최종', 'final_score')}
                ${scoreBar('기술 지표', 'tech_score', 'BASIC')}
                ${scoreBar('거래소 합의', 'exchange_consensus_pct', 'BASIC')}
                ${scoreBar('위험', 'risk_score', 'BASIC')}
            </div>
            <p class="meta" style="margin:14px 0 0;color:var(--muted);font-size:12px">
                최종 점수는 정해진 방향을 얼마나 강하게 지지하는지를 나타냅니다
                (기술 56.25% · 거래소 합의 25% · 위험 18.75%).
                <br>점수는 규칙과 통계로만 산출하며, AI 는 아래 근거·위험 요소를
                설명하는 데만 사용합니다.</p>
        </div>

        <div class="grid" style="margin-top:12px">
            <div class="card"><div class="meta">글로벌 평균가</div>
                <div class="price">$${price(s.entry_price_global)}</div></div>
            <div class="card"><div class="meta">업비트</div>
                <div class="price">${s.entry_price_upbit ? '₩' + price(s.entry_price_upbit) : '—'}</div></div>
            <div class="card"><div class="meta">거래소 합의율</div>
                <div class="price">${fieldOr(s, 'exchange_consensus_pct', (v) => num(v, 0) + '%', 'BASIC')}</div>
                <div class="meta">${'data_sources' in s && s.data_sources
                    ? esc((s.data_sources.exchanges || []).join(', ')) : ''}</div></div>
            ${probabilityCard(s)}
        </div>

        <h2>근거</h2>
        <div class="card"><ul style="margin:0;padding-left:18px">${reasons || '<li>—</li>'}</ul></div>
        <h2>위험 요소</h2>
        <div class="card">${risks}</div>`, '#/signals');
}

async function viewBacktest() {
    let reference = 'GLOBAL_CONSENSUS';

    const render = async () => {
        app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/backtest');
        const data = await call(`/backtest/logs?reference_exchange=${reference}`);

        const rows = data.backtests.map((b) => {
            // 비용 반영 여부를 숫자 옆에 함께 보여준다. 수수료·슬리피지를 빼고 낸 수익률과
            // 반영한 수익률은 완전히 다른 값이라, 표기 없이 % 만 두면 오해를 부른다.
            const cost = b.params?.cost_model;
            const costText = cost
                ? `수수료 ${num(cost.fee_rate * 100, 3)}% · 슬리피지 ${num(cost.slippage_rate * 100, 3)}%`
                : '비용 정보 없음';
            return `
            <tr>
                <td><strong>${esc(b.symbol)}</strong></td>
                <td style="color:var(--muted)">${esc(b.params?.timeframe || '—')}</td>
                <td style="color:var(--muted)">${esc(b.start_date)} ~ ${esc(b.end_date)}</td>
                <td class="num">${b.total_trades}</td>
                <td class="num ${Number(b.total_return_pct) >= 0 ? 'up' : 'down'}">
                    ${Number(b.total_return_pct) >= 0 ? '▲' : '▼'} ${num(b.total_return_pct)}%</td>
                <td class="num">${num(b.win_rate, 1)}%</td>
                <td class="num">${b.avg_profit_loss_ratio === null ? '—' : num(b.avg_profit_loss_ratio)}</td>
                <td class="num">${num(b.mdd)}%</td>
                <td style="color:var(--muted);font-size:12px">${esc(costText)}</td>
            </tr>`;
        }).join('');

        app().innerHTML = shell(`
            <h1>백테스트</h1>
            <div class="msg ok" style="margin-bottom:14px">${esc(BACKTEST_NOTE)}</div>
            <p class="sub">기준이 다르면 비용 모델과 체결 가격이 달라 숫자를 비교할 수 없습니다.
                그래서 한 표에 섞지 않습니다.
                데이터 출처는 각 거래소 공개 API 이며, 신호는 운영과 같은 룰 엔진으로 재생합니다.</p>
            <div class="tabs">
                <button class="${reference === 'GLOBAL_CONSENSUS' ? 'on' : ''}" data-ref="GLOBAL_CONSENSUS">
                    신호 검증용 (거래소 합의)</button>
                <button class="${reference === 'upbit' ? 'on' : ''}" data-ref="upbit">
                    업비트 실전용 (수수료·슬리피지 반영)</button>
            </div>
            ${rows ? `<div class="table-wrap"><table><thead><tr>
                <th>심볼</th><th>봉</th><th>구간</th><th class="num">거래</th>
                <th class="num">시뮬레이션 수익률</th><th class="num">승률</th>
                <th class="num">손익비</th><th class="num">MDD</th><th>비용 반영</th>
            </tr></thead><tbody>${rows}</tbody></table></div>`
            : '<div class="empty">이 기준으로 실행된 백테스트가 없습니다.</div>'}`, '#/backtest');

        document.querySelectorAll('.tabs button').forEach((btn) => {
            btn.onclick = () => { reference = btn.dataset.ref; render(); };
        });
    };

    await render();
}

/**
 * 점수 추이 스파크라인. 라이브러리를 쓰지 않고 인라인 SVG 로 그린다.
 *
 * 차트 하나에 번들러와 의존성을 들이지 않는다(9-a 의 "빌드 도구 없음"과 같은 이유).
 * 값이 2개 미만이면 선이 되지 않으므로 그리지 않는다.
 */
function sparkline(points, { width = 640, height = 120 } = {}) {
    if (!points || points.length < 2) {
        return '<div class="empty">추이를 그릴 만큼 신호가 쌓이지 않았습니다.</div>';
    }
    const values = points.map((p) => p.value);
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 100);
    const span = max - min || 1;
    const stepX = width / (points.length - 1);

    const coords = points.map((p, i) => [
        i * stepX,
        height - ((p.value - min) / span) * height,
    ]);
    const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `${line} L${width},${height} L0,${height} Z`;

    // 진입 기준선(80). 점수가 이 위로 올라가야 진입 조건이 된다.
    const thresholdY = height - ((80 - min) / span) * height;
    const dots = coords.map(([x, y], i) =>
        `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5"
                 fill="var(--accent)"><title>${esc(points[i].label)}: ${points[i].value}</title></circle>`
    ).join('');

    return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"
                 style="width:100%;height:${height}px;overflow:visible" role="img"
                 aria-label="최종 점수 추이">
        <line x1="0" y1="${thresholdY.toFixed(1)}" x2="${width}" y2="${thresholdY.toFixed(1)}"
              stroke="var(--border)" stroke-dasharray="4 4"/>
        <path d="${area}" fill="var(--accent-soft)"/>
        <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2"
              stroke-linejoin="round"/>
        ${dots}
    </svg>
    <div class="row" style="justify-content:space-between;color:var(--muted);font-size:12px">
        <span>${esc(points[0].label)}</span>
        <span>진입 기준선 80 (점선)</span>
        <span>${esc(points[points.length - 1].label)}</span>
    </div>`;
}

/**
 * 모델 추정 확률 카드.
 *
 * 확률 합계가 규격을 벗어나면 모델이 분포를 제대로 못 낸 것이다. 그 숫자를 화면에
 * 띄우면 유저가 근거 없는 값을 근거로 판단하게 되므로 **아예 보여주지 않는다.**
 * 기록은 DB 에 남아 있다(모델이 얼마나 자주 이러는지 세야 하므로).
 */
function probabilityCard(s) {
    const reliable = s.data_sources?.ai?.probabilities_reliable;
    if (reliable === false) {
        return `<div class="card"><div class="meta">모델 추정 확률</div>
            <div class="price" style="font-size:15px">${pending('산출 불가')}</div>
            <div class="meta" style="margin-top:6px">
                모델이 유효한 확률 분포를 내지 못해 표시하지 않습니다.</div></div>`;
    }
    if (s.up_prob === undefined || s.up_prob === null) {
        return `<div class="card"><div class="meta">모델 추정 확률</div>
            <div class="price" style="font-size:15px">${locked('BASIC')}</div></div>`;
    }
    return `<div class="card"><div class="meta">모델 추정 확률 (상승 / 보합 / 하락)</div>
        <div class="price" style="font-size:16px">
            <span class="up">${num(s.up_prob, 0)}%</span> ·
            ${num(s.sideways_prob, 0)}% ·
            <span class="down">${num(s.down_prob, 0)}%</span></div>
        <div class="meta" style="margin-top:6px">${esc(MODEL_OUTPUT_NOTE)}</div></div>`;
}

/* --- 관리자 -------------------------------------------------------------- */

async function viewAdmin() {
    app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/admin');

    const [users, budget, system, safety] = await Promise.all([
        call('/admin/users'),
        call('/admin/ai-budget'),
        call('/admin/system'),
        call('/safety/state'),
    ]);

    const c = system.system.counts;
    const summary = [
        ['유저', c.users], ['신호(전체)', c.signals], ['신호(24시간)', c.signals_24h],
        ['백테스트', c.backtests], ['웹훅 수신', c.webhook_signals],
    ].map(([label, value]) => `<div class="card">
        <div class="meta">${label}</div><div class="price">${num(value, 0)}</div></div>`).join('');

    // 상태 배지는 `.status` 를 쓴다. `.sig` 는 시장 방향 색이라 로케일에 따라 뒤집힌다.
    const engine = system.system.engine_alive
        ? `<span class="status ok">● 수집 중</span> ${system.system.symbols.length}개 심볼`
        : `<span class="status bad">● 중단</span> 시세 캐시가 비어 있습니다`;

    const modeButtons = budget.ai_budget.modes.map((m) => `
        <button class="${budget.ai_budget.mode === m ? 'on' : ''}" data-mode="${m}">${m}</button>`
    ).join('');

    const userRows = users.users.map((u) => `
        <tr>
            <td>#${u.id}</td>
            <td>${esc(u.email)}</td>
            <td>${esc(u.name)}</td>
            <td>${u.role === 'admin' ? '<span class="plan-tag">ADMIN</span>' : ''}</td>
            <td><span class="plan-tag">${esc(u.plan.toUpperCase())}</span></td>
            <td>
                <select data-user="${u.id}">
                    ${['free', 'basic', 'pro'].map((p) =>
                        `<option value="${p}" ${u.plan === p ? 'selected' : ''}>${p}</option>`).join('')}
                </select>
            </td>
        </tr>`).join('');

    app().innerHTML = shell(`
        <h1>관리</h1>
        <p class="sub">운영 현황과 비용·안전장치 제어</p>
        <div class="grid">${summary}</div>

        <h2>엔진</h2>
        <div class="card">${engine}</div>

        <h2>AI 호출 예산</h2>
        <div class="card">
            <p class="sub" style="margin-bottom:12px">
                유저가 없는 동안 나가는 유일한 고정비입니다.
                <strong>off</strong> 는 호출 0(룰 신호는 계속 나옴),
                <strong>seed</strong> 는 심볼당 하루 5건(적중률 기록 유지),
                <strong>full</strong> 은 게이트 통과 전부입니다.
            </p>
            <div class="tabs" id="modes">${modeButtons}</div>
            <div class="meta">현재: <strong>${esc(budget.ai_budget.effective)}</strong>
                · seed 슬롯 사용 중 ${budget.ai_budget.seed_slots_in_use}개
                ${budget.ai_budget.mode === null
                    ? '<br>재정의가 없어 엔진 <code>.env</code> 기본값으로 동작합니다.' : ''}</div>
            <div id="budgetMsg"></div>
        </div>

        <h2>실거래 안전장치</h2>
        <div class="card">
            <div class="row" style="gap:20px">
                <div><div class="meta">모드</div>
                    <div class="price" style="font-size:18px">${esc(safety.safety.mode)}</div></div>
                <div><div class="meta">Kill switch</div>
                    <div class="price" style="font-size:18px">
                        ${safety.safety.kill_switch_active
                            ? '<span class="status bad">● 작동 중</span>' : '○ 해제'}
                    </div></div>
                <div><div class="meta">일일 손실 한도</div>
                    <div class="price" style="font-size:18px">${num(safety.safety.daily_loss_limit_pct)}%</div></div>
                <div><div class="meta">단일 주문 한도</div>
                    <div class="price" style="font-size:18px">₩${price(safety.safety.max_position_size_krw)}</div></div>
            </div>
            <div class="row" style="margin-top:14px">
                <button id="toggleMode" class="ghost">
                    ${safety.safety.mode === 'PAPER' ? 'LIVE 로 전환' : 'PAPER 로 되돌리기'}</button>
                <button id="toggleKill" class="ghost">
                    ${safety.safety.kill_switch_active ? 'Kill switch 해제' : 'Kill switch 작동'}</button>
            </div>
            <div class="meta" style="margin-top:10px">
                매매 내역(보유 코인·손익·잔고)은 매매 엔진이 붙는 Step 5 이후에 표시됩니다.
            </div>
            <div id="safetyMsg"></div>
        </div>

        <h2>유저</h2>
        <div class="table-wrap"><table>
            <thead><tr><th>ID</th><th>이메일</th><th>이름</th><th>역할</th>
                <th>현재 등급</th><th>등급 변경</th></tr></thead>
            <tbody>${userRows}</tbody></table></div>
        <p class="sub" style="margin-top:10px">
            역할(관리자) 변경은 화면에서 하지 않습니다 —
            승격 API 가 존재하는 것 자체가 권한 상승 취약점이라 CLI 로만 처리합니다.</p>`, '#/admin');

    document.querySelectorAll('#modes button').forEach((btn) => {
        btn.onclick = async () => {
            try {
                await call('/admin/ai-budget', { method: 'PATCH', body: { mode: btn.dataset.mode } });
                viewAdmin();
            } catch (error) {
                document.getElementById('budgetMsg').innerHTML =
                    `<div class="msg err">${esc(error.message)}</div>`;
            }
        };
    });

    document.querySelectorAll('select[data-user]').forEach((sel) => {
        sel.onchange = async () => {
            try {
                await call(`/admin/users/${sel.dataset.user}`, {
                    method: 'PATCH', body: { plan: sel.value, days: 30 },
                });
                viewAdmin();
            } catch (error) { alert(error.message); }
        };
    });

    const patchSafety = async (body) => {
        try {
            await call('/safety/state', { method: 'PATCH', body });
            viewAdmin();
        } catch (error) {
            document.getElementById('safetyMsg').innerHTML =
                `<div class="msg err">${esc(error.message)}</div>`;
        }
    };
    document.getElementById('toggleMode').onclick = () => {
        const next = safety.safety.mode === 'PAPER' ? 'LIVE' : 'PAPER';
        // LIVE 전환은 실제 돈이 움직이기 시작하는 지점이다. 한 번 되묻는다.
        if (next === 'LIVE' && !confirm('실거래(LIVE)로 전환합니다. 실제 주문이 발생합니다. 계속할까요?')) return;
        patchSafety({ mode: next });
    };
    document.getElementById('toggleKill').onclick = () =>
        patchSafety({ kill_switch_active: !safety.safety.kill_switch_active });
}

/* --- 적중률 (관리자 전용) ------------------------------------------------- */

/**
 * Step 7 이 기록한 실제 결과를 보는 화면.
 *
 * **유저에게 노출하지 않는다** — 표현 문구가 법률 검토 대상이다. 지금은 운영자가
 * 스스로 이 도구를 신뢰할 수 있는지 판단하기 위한 내부 화면이다. 그래서 좋아 보이게
 * 꾸미지 않고 **불리한 숫자를 먼저 보여준다**(표본 부족 · 시간초과 비중 · 점수 무관계).
 */
const ACCURACY_STATE = { days: 30, version: null };

const EXIT_LABEL = {
    TAKE_PROFIT: '<span class="status ok">익절</span>',
    STOP_LOSS: '<span class="status bad">손절</span>',
    TIME_LIMIT: '<span class="pending">시간초과</span>',
};

/** 표본이 적으면 통계로 읽으면 안 된다. 회색 처리하고 이유를 남긴다. */
function sample(row, text) {
    return row.small_sample
        ? `<span class="pending" title="표본 부족 — 통계로 읽지 마세요">${text}</span>`
        : text;
}

function pct(value) {
    return value === null || value === undefined ? '—' : `${num(value, 1)}%`;
}

/** 무엇으로 끝났는지가 승률보다 많은 것을 말해준다. */
function exitMix(row) {
    const total = row.evaluated || 1;
    const bar = (n, cls) => (n === 0 ? '' :
        `<span class="mix ${cls}" style="width:${((n / total) * 100).toFixed(1)}%"></span>`);
    return `<div class="mixbar" title="익절 ${row.take_profit} / 손절 ${row.stop_loss} / 시간초과 ${row.time_limit}">${
        bar(row.take_profit, 'tp')}${bar(row.stop_loss, 'sl')}${bar(row.time_limit, 'tl')}</div>`;
}

const ACCURACY_HEAD = `<thead><tr>
    <th></th><th>평가</th><th>적중률</th><th>평균 손익</th>
    <th>익절/손절/시간초과</th><th></th></tr></thead>`;

function accuracyRows(rows, labelKey, labelFn) {
    if (!rows.length) return '<tr><td colspan="6">기록 없음</td></tr>';
    return rows.map((r) => `
        <tr>
            <td>${labelFn ? labelFn(r) : esc(String(r[labelKey]))}</td>
            <td>${r.evaluated}</td>
            <td><strong>${sample(r, pct(r.accuracy_pct))}</strong></td>
            <td>${num(r.avg_return, 2)}%</td>
            <td>${r.take_profit} / ${r.stop_loss} / ${r.time_limit}</td>
            <td>${exitMix(r)}</td>
        </tr>`).join('');
}

async function viewAccuracy() {
    app().innerHTML = shell('<p class="sub">불러오는 중…</p>', '#/accuracy');

    const query = new URLSearchParams({ days: String(ACCURACY_STATE.days) });
    if (ACCURACY_STATE.version) query.set('version', ACCURACY_STATE.version);
    const { accuracy: a } = await call(`/admin/accuracy?${query}`);
    ACCURACY_STATE.version = a.version;

    const dayTabs = [7, 30, 90, 365].map((d) =>
        `<button class="${a.days === d ? 'on' : ''}" data-days="${d}">${d}일</button>`).join('');
    const versionTabs = a.versions.map((v) =>
        `<button class="${a.version === v ? 'on' : ''}" data-version="${esc(v)}">배점표 ${esc(v)}</button>`
    ).join('') || '<span class="meta">평가된 신호가 아직 없습니다</span>';

    const b = a.backlog;
    const progress = b.expected ? Math.round((b.evaluated / b.expected) * 100) : 0;

    // 표제 표. **배점표를 나눠서 보여주는 것이 이 화면의 존재 이유다.**
    const horizonRows = a.by_horizon.length
        ? a.by_horizon.map((r) => `
            <tr>
                <td><span class="plan-tag">${esc(r.version)}</span></td>
                <td>${esc(r.horizon)}</td>
                <td>${r.evaluated}</td>
                <td><strong>${sample(r, pct(r.accuracy_pct))}</strong></td>
                <td>${num(r.avg_return, 2)}%</td>
                <td>${r.take_profit} / ${r.stop_loss} / ${r.time_limit}</td>
                <td>${exitMix(r)}</td>
            </tr>`).join('')
        : '<tr><td colspan="7">아직 평가된 신호가 없습니다</td></tr>';

    const recentRows = a.recent.length ? a.recent.map((r) => `
        <tr>
            <td>#${r.signal_id}</td>
            <td>${esc(r.symbol)}</td>
            <td>${signalBadge(r.signal_type)}</td>
            <td>${r.final_score}</td>
            <td>${esc(r.horizon)}</td>
            <td>${num(r.return_pct, 2)}%</td>
            <td>${EXIT_LABEL[r.exit_reason] || '—'}</td>
            <td>${Number(r.is_accurate) === 1
                ? '<span class="status ok">적중</span>' : '<span class="status bad">빗나감</span>'}</td>
            <td class="meta">${when(r.created_at)}</td>
        </tr>`).join('') : '<tr><td colspan="9">기록 없음</td></tr>';

    app().innerHTML = shell(`
        <h1>적중률</h1>
        <p class="sub">Step 7 성과 추적이 기록한 <strong>실제 결과</strong>입니다.
            백테스트가 아닙니다. 관리자 전용 화면입니다.</p>

        <div class="tabs" id="dayTabs">${dayTabs}</div>
        <div class="tabs" id="versionTabs">${versionTabs}</div>

        <div class="card">
            <div class="row" style="gap:24px">
                <div><div class="meta">평가 완료</div>
                    <div class="price" style="font-size:18px">${num(b.evaluated, 0)}
                        <span class="meta">/ ${num(b.expected, 0)} (${progress}%)</span></div></div>
                <div><div class="meta">마지막 평가</div>
                    <div class="price" style="font-size:18px">${when(b.last_evaluated_at)}</div></div>
            </div>
            <div class="meta" style="margin-top:10px">
                신호 하나당 시간제한 5종(5m·15m·1h·4h·1d)을 각각 평가합니다.
                긴 제한은 그만큼 시간이 지나야 채워집니다 — 1d 는 24시간 뒤입니다.
            </div>
        </div>

        <h2>배점표 × 시간제한</h2>
        <p class="sub">배점표가 다르면 <strong>다른 규칙으로 만든 다른 신호</strong>입니다.
            섞어서 평균 내면 "고쳤더니 나아졌는가"를 알 수 없어 항상 나눠서 봅니다.</p>
        <div class="table-wrap"><table>
            <thead><tr><th>배점표</th><th>제한</th><th>평가</th><th>적중률</th>
                <th>평균 손익</th><th>익절/손절/시간초과</th><th></th></tr></thead>
            <tbody>${horizonRows}</tbody></table></div>

        <h2>점수 구간별 <span class="meta">— 점수가 높을수록 좋은 신호인가</span></h2>
        <p class="sub">백테스트(Step 16)에서는 <strong>v2·v3 모두 점수가 품질을 제대로
            가르지 못했습니다.</strong> 운영 데이터에서도 그런지 지켜보는 표입니다.
            아래로 갈수록 적중률이 올라가지 않으면 점수를 확신도로 읽으면 안 됩니다.</p>
        <div class="table-wrap"><table>${ACCURACY_HEAD}
            <tbody>${accuracyRows(a.by_score_bucket, 'bucket',
        (r) => `${r.bucket}~${r.bucket + 9}점`)}</tbody></table></div>

        <h2>심볼별</h2>
        <div class="table-wrap"><table>${ACCURACY_HEAD}
            <tbody>${accuracyRows(a.by_symbol, 'symbol')}</tbody></table></div>

        <h2>신호 종류별</h2>
        <div class="table-wrap"><table>${ACCURACY_HEAD}
            <tbody>${accuracyRows(a.by_signal_type, 'signal_type',
        (r) => signalBadge(r.signal_type))}</tbody></table></div>

        <h2>최근 개별 결과 <span class="meta">— 최대 50건</span></h2>
        <p class="sub">집계만 보면 무엇이 이상한지 알 수 없습니다.</p>
        <div class="table-wrap"><table>
            <thead><tr><th>신호</th><th>심볼</th><th>종류</th><th>점수</th><th>제한</th>
                <th>손익</th><th>청산</th><th>판정</th><th>생성</th></tr></thead>
            <tbody>${recentRows}</tbody></table></div>

        <div class="card" style="margin-top:18px">
            <div class="meta">
                <strong>읽을 때 주의.</strong>
                표본이 ${a.small_sample}건 미만인 줄은 회색입니다 — 통계가 아닙니다.
                <strong>시간초과</strong>가 대부분이면 그 적중률은 "익절·손절 어디에도 닿지 않은 채
                시간이 다 됐을 때 방향이 맞았다"는 뜻이지, 그 폭만큼 벌었다는 뜻이 아닙니다.
                왕복 거래 비용이 약 0.18%이므로 평균 손익이 그보다 작으면 실거래에서는 손실입니다.
            </div>
        </div>`, '#/accuracy');

    document.querySelectorAll('#dayTabs button').forEach((btn) => {
        btn.onclick = () => { ACCURACY_STATE.days = Number(btn.dataset.days); viewAccuracy(); };
    });
    document.querySelectorAll('#versionTabs button').forEach((btn) => {
        btn.onclick = () => { ACCURACY_STATE.version = btn.dataset.version; viewAccuracy(); };
    });
}

/* --- 라우팅 -------------------------------------------------------------- */

async function route() {
    const hash = location.hash || '#/';

    if (!state.token) {
        if (hash !== '#/login') { location.hash = '#/login'; return; }
        return viewLogin();
    }
    if (hash === '#/login') { location.hash = '#/'; return; }

    // 새로고침으로 들어오면 유저 정보가 없다. 토큰이 아직 유효한지도 여기서 확인된다.
    if (!state.user) {
        try {
            const me = await call('/auth/me');
            state.user = me.user;
            state.plan = me.plan;
            document.documentElement.dataset.locale = me.user.locale || 'ko';
        } catch {
            return;  // call() 이 401 이면 이미 로그아웃 처리했다
        }
    }

    try {
        const detail = hash.match(/^#\/signals\/(\d+)$/);
        if (detail) return await viewSignalDetail(detail[1]);
        if (hash.startsWith('#/signals')) return await viewSignals();
        if (hash.startsWith('#/backtest')) return await viewBacktest();
        if (hash.startsWith('#/admin')) return await viewAdmin();
        if (hash.startsWith('#/accuracy')) return await viewAccuracy();
        return await viewDashboard();
    } catch (error) {
        app().innerHTML = shell(
            `<div class="msg err">${esc(error.message)}</div>`,
            hash
        );
    }
}

window.__logout = () => logout(true);
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);
