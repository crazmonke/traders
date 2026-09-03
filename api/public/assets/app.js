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

const SIGNAL_LABEL = {
    STRONG_BUY: { cls: 'buy', mark: '▲▲', text: '적극 매수' },
    BUY: { cls: 'buy', mark: '▲', text: '매수' },
    HOLD: { cls: 'hold', mark: '―', text: '관망' },
    SELL: { cls: 'sell', mark: '▼', text: '매도' },
    STRONG_SELL: { cls: 'sell', mark: '▼▼', text: '적극 매도' },
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

function when(ts) {
    if (!ts) return '—';
    const d = new Date(ts.replace(' ', 'T') + 'Z');
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

/* --- 화면 ---------------------------------------------------------------- */

const app = () => document.getElementById('app');

function shell(inner, active) {
    const nav = [
        ['#/', '대시보드'],
        ['#/signals', '신호'],
        ['#/backtest', '백테스트'],
    ].map(([href, label]) =>
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
    const data = await call('/signals/latest?limit=50');

    const notice = state.plan === 'free'
        ? `<div class="msg ok">FREE 등급은 신호가 15분 지연되고 근거가 일부 가려집니다.
           실시간과 전체 근거는 BASIC 이상에서 볼 수 있습니다.</div>`
        : '';

    app().innerHTML = shell(`
        <h1>신호</h1>
        <p class="sub">최신순 · ${data.signals.length}건</p>
        ${notice}
        ${signalTable(data.signals)}`, '#/signals');
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
                ${scoreBar('AI', 'ai_score', 'BASIC')}
                ${scoreBar('위험', 'risk_score', 'BASIC')}
            </div>
            <p class="meta" style="margin:14px 0 0;color:var(--muted);font-size:12px">
                최종 점수는 정해진 방향을 얼마나 강하게 지지하는지를 나타냅니다
                (기술 45% · 합의 20% · AI 20% · 위험 15%).</p>
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
            <div class="card"><div class="meta">상승 / 보합 / 하락</div>
                <div class="price" style="font-size:16px">
                    <span class="up">${num(s.up_prob, 0)}%</span> ·
                    ${num(s.sideways_prob, 0)}% ·
                    <span class="down">${num(s.down_prob, 0)}%</span></div></div>
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

        const rows = data.backtests.map((b) => `
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
            </tr>`).join('');

        app().innerHTML = shell(`
            <h1>백테스트</h1>
            <p class="sub">기준이 다르면 비용 모델과 체결 가격이 달라 숫자를 비교할 수 없습니다.
                그래서 한 표에 섞지 않습니다.</p>
            <div class="tabs">
                <button class="${reference === 'GLOBAL_CONSENSUS' ? 'on' : ''}" data-ref="GLOBAL_CONSENSUS">
                    신호 검증용 (거래소 합의)</button>
                <button class="${reference === 'upbit' ? 'on' : ''}" data-ref="upbit">
                    업비트 실전용 (수수료·슬리피지 반영)</button>
            </div>
            ${rows ? `<div class="table-wrap"><table><thead><tr>
                <th>심볼</th><th>봉</th><th>구간</th><th class="num">거래</th>
                <th class="num">수익률</th><th class="num">승률</th>
                <th class="num">손익비</th><th class="num">MDD</th>
            </tr></thead><tbody>${rows}</tbody></table></div>`
            : '<div class="empty">이 기준으로 실행된 백테스트가 없습니다.</div>'}`, '#/backtest');

        document.querySelectorAll('.tabs button').forEach((btn) => {
            btn.onclick = () => { reference = btn.dataset.ref; render(); };
        });
    };

    await render();
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
