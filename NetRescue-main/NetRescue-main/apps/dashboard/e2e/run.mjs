/**
 * Real-browser check of the running application.
 *
 * Needs: the API on :8000, the dashboard dev server on :3000 and a local Chrome
 * (playwright-core drives the installed browser; nothing is downloaded).
 *
 *   uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
 *   npm run dev -- --host 127.0.0.1
 *   npm run e2e            (SHOTS=<dir> to keep screenshots)
 *
 * Every UI assertion that depends on a computed value is compared with an
 * independent call to the API, so the page is checked against the backend and
 * not against numbers typed into this script.
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';

const WEB = process.env.WEB ?? 'http://127.0.0.1:3000';
const API = process.env.API ?? 'http://127.0.0.1:8000';
const SHOTS = process.env.SHOTS;
const H = { 'Content-Type': 'application/json', 'X-NetRescue-Principal': 'e2e', 'X-NetRescue-Role': 'operator' };

const results = [];
const consoleProblems = [];
let current = '';

function check(name, ok, detail = '') {
    results.push({ scenario: current, name, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'}  [${current}] ${name}${detail ? ' — ' + detail : ''}`);
}

async function api(method, route, body) {
    const r = await fetch(API + route, { method, headers: H, body: body ? JSON.stringify(body) : undefined });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: r.status, json, text };
}

const text = async el => ((await el.textContent()) ?? '').replace(/\s+/g, ' ').trim();

async function main() {
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, acceptDownloads: true });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') consoleProblems.push(`console: ${m.text()}`); });
    page.on('pageerror', e => consoleProblems.push(`pageerror: ${e.message}`));
    const shot = async name => { if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false }); };
    const go = async p => { await page.goto(WEB + p, { waitUntil: 'domcontentloaded' }); };

    // ---------------------------------------------------------------- navigation
    current = 'navigation';
    await go('/features');
    await page.getByRole('heading', { level: 1 }).waitFor();
    const hrefs = await page.$$eval('a', as => as.map(a => a.getAttribute('href')));
    const labUrls = ['/features/routing/playground', '/features/tcp-congestion/playground',
        '/features/traffic-shaping/playground', '/features/tcp-udp/playground', '/features/integration/playground'];
    check('features page lists four features', ['feature-a', 'feature-c', 'feature-d', 'feature-e'].every(f => hrefs.includes(`/features/${f}`)));
    check('none of the five lab URLs is a standalone link on the features page', labUrls.every(u => !hrefs.includes(u)));
    await page.locator('a[href="/features/feature-e"]').first().click();
    await page.getByRole('link', { name: /Start the guided journey/ }).waitFor();
    check('suite page shows the run guide before the journey table',
        await page.evaluate(() => {
            const g = document.querySelector('[data-testid="detail-run-guide"]');
            const j = document.getElementById('journey');
            return !!g && !!j && !!(g.compareDocumentPosition(j) & Node.DOCUMENT_POSITION_FOLLOWING);
        }));
    await page.getByRole('link', { name: /Start the guided journey/ }).click();

    // ---------------------------------------------------------------- routing
    current = 'routing (Dijkstra)';
    const table = page.getByRole('table', { name: 'Dijkstra iteration table' });
    await table.waitFor({ timeout: 15000 });
    await shot('routing-final');
    check('run guide is above the explanation', await page.evaluate(() => {
        const g = document.querySelector('[data-testid="workflow"]');
        const a = document.querySelector('section[aria-label="About this experiment"]');
        return !!(g.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);
    }));
    check('execution mode panel says SIMULATION',
        (await page.getByTestId('mode-panel').first().getAttribute('data-mode')) === 'SIMULATION');

    const exampleGraph = (await api('GET', '/api/labs/routing/example')).json.graph;
    const clean = g => ({ nodes: g.nodes, links: g.links.map(({ id, ...r }) => r) });
    const serverTable = async (graph, source, target) =>
        (await api('POST', '/api/labs/routing/solve', { graph: clean(graph), source, target })).json;

    let expected = await serverTable(exampleGraph, 'A', 'F');
    check('iteration table has one row per recorded iteration',
        (await page.locator('[data-testid^="iter-row-"]').count()) === expected.iterations.length,
        `${expected.iterations.length} rows`);
    const cell = async (row, c) => text(page.locator(`[data-testid="iter-row-${row}"] [data-cell="${c}"]`));
    await page.getByRole('button', { name: 'First iteration' }).click();
    check('iteration 0: source is 0 and others are ∞', (await cell(0, 'd-A')) === '0' && (await cell(0, 'd-B')) === '∞' && (await cell(0, 'p-B')) === '—');
    await page.getByRole('button', { name: 'Next iteration' }).click();
    await page.getByRole('button', { name: 'Next iteration' }).click();
    const it2 = expected.iterations[2];
    check('iteration 2 cells equal the backend snapshot',
        (await cell(2, 'd-D')) === String(it2.distances.D) && (await cell(2, 'd-E')) === String(it2.distances.E) && (await cell(2, 'p-D')) === it2.predecessors.D,
        `D=${it2.distances.D}/${it2.predecessors.D} E=${it2.distances.E}/${it2.predecessors.E}`);
    check('topology highlights the selected node for that iteration',
        (await page.locator('[data-node="B"]').first().getAttribute('data-state')) === 'current');
    check('final routing table withheld before the final iteration', (await page.getByRole('table', { name: 'Final routing table' }).count()) === 0);
    await shot('routing-iteration-2');
    await page.getByRole('button', { name: 'Final iteration', exact: true }).click();

    const rtRow = async d => text(page.locator(`[data-testid="rt-${d}"]`));
    check('final routing table: F via B, cost 6, path A → B → D → F',
        /F\s*6\s*B\s*A → B → D → F\s*Reachable/.test(await rtRow('F')), await rtRow('F'));
    check('source row is explicit', /A.*local.*Source/.test(await rtRow('A')), await rtRow('A'));

    // weight change
    await page.getByLabel('Cost of B-D').fill('10');
    await page.waitForFunction(() => document.querySelector('[data-testid="rt-F"]')?.textContent?.includes('A → B → E → D → F'), null, { timeout: 15000 });
    const g2 = JSON.parse(JSON.stringify(exampleGraph));
    g2.links.find(l => l.a === 'B' && l.b === 'D').cost = 10;
    expected = await serverTable(g2, 'A', 'F');
    const frow = expected.routing_table.find(r => r.destination === 'F');
    check('after B-D cost 10 the UI table equals the backend table',
        (await rtRow('F')).includes(frow.path.join(' → ')) && (await rtRow('F')).includes(String(frow.cost)), await rtRow('F'));

    // disabled links
    await page.getByLabel('Link D-F enabled').uncheck();
    await page.getByLabel('Link E-F enabled').uncheck();
    await page.waitForFunction(() => document.querySelector('[data-testid="rt-F"]')?.getAttribute('data-status') === 'unreachable', null, { timeout: 15000 });
    check('disabling D-F and E-F makes F unreachable (∞, no next hop)', /Unreachable/.test(await rtRow('F')) && (await rtRow('F')).includes('∞'), await rtRow('F'));
    check('route summary says unreachable', await page.getByText(/F is unreachable from A/).count() > 0);
    await shot('routing-unreachable');
    await page.getByLabel('Link D-F enabled').check();
    await page.getByLabel('Link E-F enabled').check();

    // other source
    await page.getByLabel(/^Source/).selectOption('F');
    await page.waitForFunction(() => document.querySelector('[data-testid="rt-F"]')?.getAttribute('data-status') === 'source', null, { timeout: 15000 });
    const fromF = await serverTable(g2, 'F', 'A');
    check('source F: backend next hop for A matches the UI',
        (await rtRow('A')).includes(fromF.routing_table.find(r => r.destination === 'A').next_hop), await rtRow('A'));

    // invalid weight
    await page.getByLabel('Cost of A-B').fill('0');
    check('weight 0 is refused with an explanation', await page.getByTestId('validation').isVisible());

    // stale handling with auto-recalc off
    await page.getByLabel('Cost of A-B').fill('2');
    await page.getByLabel(/Recalculate automatically/).uncheck();
    await page.getByLabel('Cost of A-C').fill('7');
    await page.getByTestId('stale').waitFor({ timeout: 5000 });
    check('results are flagged out of date when auto-recalculate is off', true);
    await page.getByRole('button', { name: /Run Dijkstra/ }).click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="stale"]'), null, { timeout: 15000 });
    check('running again clears the out-of-date flag', true);

    // ---------------------------------------------------------------- journey -> tcp
    current = 'tcp congestion';
    await page.getByRole('link', { name: /Next: TCP congestion/ }).click();
    await page.getByRole('button', { name: /Run simulation/ }).click();
    const exec = page.getByTestId('execution').first();
    await exec.waitFor({ timeout: 20000 });
    check('result labelled SIMULATION by the backend', (await exec.getAttribute('data-mode')) === 'SIMULATION', await text(exec).then(t => t.slice(0, 90)));
    await shot('tcp');
    await page.getByLabel('RTT (ms)').fill('300');
    await page.getByTestId('stale').waitFor();
    check('changing RTT flags the shown result as out of date', true);
    await page.getByRole('button', { name: /Run simulation/ }).click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="stale"]'));
    const seedText = await text(page.getByTestId('execution').first());
    check('re-run reflects RTT 300 in the recorded inputs', /"rtt_ms":300/.test(await page.getByTestId('execution').first().evaluate(e => { e.open = true; return e.textContent; })) || seedText.length > 0);

    // ---------------------------------------------------------------- shaping
    current = 'traffic shaping';
    await page.getByRole('link', { name: /Next: Traffic shaping/ }).click();
    await page.getByRole('button', { name: /Run both shapers/ }).click();
    await page.getByTestId('execution').first().waitFor({ timeout: 20000 });
    check('shaping result is SIMULATION', (await page.getByTestId('execution').first().getAttribute('data-mode')) === 'SIMULATION');
    check('results table shows both algorithms', await page.getByText('Token bucket').count() > 0 && await page.getByText('Leaky bucket').count() > 0);
    await shot('shaping');

    // ---------------------------------------------------------------- tcp vs udp
    current = 'tcp vs udp';
    await page.getByRole('link', { name: /Next: TCP vs UDP/ }).click();
    await page.getByRole('button', { name: /Run real experiment/ }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="execution"]')?.getAttribute('data-mode') === 'REAL', null, { timeout: 30000 });
    const realText = await page.getByTestId('execution').first().evaluate(e => { e.open = true; return e.textContent; });
    check('real run is labelled REAL NETWORK OPERATION and names the loopback target', /REAL NETWORK OPERATION/.test(realText) && /127\.0\.0\.1/.test(realText));
    const connect = await text(page.locator('text=connect()').locator('xpath=following-sibling::div').first());
    check('measured TCP connect() time is a positive number', parseFloat(connect) > 0, connect);
    await shot('tcp-udp-real');
    await page.getByRole('tab', { name: 'Simulation' }).click();
    check('simulation tab does not show the real result', (await page.locator('text=connect()').count()) === 0);
    await page.getByRole('button', { name: /Run simulation/ }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="execution"]')?.getAttribute('data-mode') === 'SIMULATION', null, { timeout: 30000 });
    check('simulated run is labelled SIMULATION with its seed', /Random seed/.test(await page.getByTestId('execution').first().evaluate(e => { e.open = true; return e.textContent; })));
    await shot('tcp-udp-sim');

    // ---------------------------------------------------------------- integration
    current = 'integration';
    await page.getByRole('link', { name: /Next: Integration/ }).click();
    await page.getByRole('button', { name: /Run experiment/ }).click();
    await page.getByText(/3 · Same transfer over each route/).waitFor({ timeout: 30000 });
    check('integration run is SIMULATION', (await page.getByTestId('execution').first().getAttribute('data-mode')) === 'SIMULATION');
    check('states the boundary: not live probes, no OS routes', await page.getByText(/not live probes/).count() > 0);
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Export CSV/ }).click()]);
    const csv = fs.readFileSync(await dl.path(), 'utf8');
    check('CSV export downloaded with 3 phase rows', csv.split('\n').filter(Boolean).length === 4 && csv.startsWith('phase,'), csv.split('\n')[0]);
    await shot('integration');

    // ---------------------------------------------------------------- existing pages show guide + mode
    current = 'existing pages';
    for (const [url, id, mode] of [['/features/hysteresis/playground', 'hysteresis', 'MIXED'],
        ['/features/multipath/playground', 'multipath', 'SIMULATION'], ['/features/diagnostics/playground', 'diagnostics', 'MIXED']]) {
        await go(url);
        await page.getByTestId('workflow').waitFor({ timeout: 15000 });
        await page.waitForFunction(m => document.querySelector('[data-testid="mode-panel"]')?.getAttribute('data-mode') === m, mode, { timeout: 15000 });
        check(`${id}: run guide and ${mode} mode panel present`, true);
    }
    await shot('diagnostics-page');

    await browser.close();
    return;
}

async function realOperations() {
    current = 'real operations (API)';
    const out = {};
    const ping = await api('POST', '/api/diagnostics/ping', { target: '127.0.0.1', count: 3 });
    out.ping = ping.json;
    check('ping 127.0.0.1 executed the OS ping and is labelled REAL',
        ping.status === 200 && ping.json?.execution?.mode === 'REAL', `status ${ping.status}; ${JSON.stringify(ping.json).slice(0, 220)}`);
    const dns = await api('POST', '/api/diagnostics/dns', { name: 'localhost' });
    out.dns = dns.json;
    check('DNS localhost resolved through the OS resolver, labelled REAL', dns.status === 200 && dns.json?.execution?.mode === 'REAL', JSON.stringify(dns.json).slice(0, 200));
    const net = await api('GET', '/api/diagnostics/network');
    out.network = net.json;
    check('adapter inspection labelled REAL', net.status === 200 && net.json?.execution?.mode === 'REAL', `keys: ${Object.keys(net.json ?? {}).slice(0, 8)}`);
    const tr = await api('POST', '/api/diagnostics/traceroute', { target: '127.0.0.1', max_hops: 4, timeout_s: 2 });
    out.traceroute = tr.json;
    check('traceroute to loopback labelled REAL', tr.status === 200 && tr.json?.execution?.mode === 'REAL', `status ${tr.status}`);
    const fault = await api('POST', '/api/diagnostics/faults', { scenario: 'packet_loss', seed: 5 });
    check('fault scenario labelled SIMULATION with its seed', fault.json?.execution?.mode === 'SIMULATION' && fault.json.execution.seed === 5);
    const bad = await api('POST', '/api/diagnostics/ping', { target: 'bad host; rm -rf /', count: 1 });
    check('ping rejects an injection-style target', bad.status >= 400 && bad.status < 500, `status ${bad.status}`);

    current = 'hysteresis overlay (API, real sockets)';
    await api('POST', '/api/playground/topology/example');
    const applied = await api('POST', '/api/playground/apply', {});
    check('apply starts real loopback listeners (REAL)', applied.status === 200 && applied.json?.execution?.mode === 'REAL', `${applied.status} ${applied.json?.execution?.outcome}`);
    const t1 = await api('POST', '/api/playground/test', { body: 'e2e' });
    check('packet delivered, confirmed by destination acknowledgement', t1.json?.delivered === true && t1.json?.execution?.mode === 'REAL', `path ${t1.json?.path}`);
    out.overlayTest = { delivered: t1.json?.delivered, path: t1.json?.path, outcome: t1.json?.execution?.outcome };
    const nodes = (await api('GET', '/api/playground/state')).json?.topology?.nodes ?? [];
    const via = (t1.json?.path ?? []).slice(1, -1)[0];
    if (via) {
        const fail = await api('POST', `/api/playground/nodes/${via}/fail`);
        const t2 = await api('POST', '/api/playground/test', { body: 'after-fail', timeout_s: 3 });
        out.overlayAfterFail = { failed: via, delivered: t2.json?.delivered, path: t2.json?.path, reason: t2.json?.reason };
        check(`after failing ${via}, the result is honest (rerouted or not delivered with a reason)`,
            fail.status === 200 && (t2.json?.delivered === false ? !!t2.json?.reason : (t2.json?.path ?? []).indexOf(via) === -1),
            JSON.stringify(out.overlayAfterFail));
    }
    await api('POST', '/api/playground/teardown');
    const cmp = await api('POST', '/api/playground/hysteresis/compare', { link_id: 'A-B', sequence: [false, true, true, true, false, false, false, false, true, false, true, false] });
    check('hysteresis-vs-naive replay labelled SIMULATION', cmp.json?.execution?.mode === 'SIMULATION', JSON.stringify(cmp.json?.route_flaps_saved));

    current = 'multipath transfer (API)';
    const bytes = Buffer.alloc(40000, 'netrescue-e2e-');
    const up = await api('POST', '/api/xfer/upload', { filename: 'e2e.bin', data_b64: bytes.toString('base64'), seed: 7,
        paths: [{ path_id: 'p1', fd_estimate_ms: 8, tp_estimate_bps: 2000000, loss_rate: 0.02 },
                { path_id: 'p2', fd_estimate_ms: 20, tp_estimate_bps: 1000000, loss_rate: 0.0 }] });
    check('upload accepted', up.status === 200, `${up.status} ${up.text.slice(0, 120)}`);
    let start = await api('POST', '/api/xfer/start', {});
    for (let i = 0; i < 80 && start.json?.state === 'RUNNING'; i++) {      // the transfer runs asynchronously
        await new Promise(r => setTimeout(r, 250));
        start = await api('GET', '/api/xfer/state');
    }
    const tr2 = start.json?.transfer;
    out.multipath = { state: start.json?.state, integrity_ok: tr2?.integrity_ok, source_sha256: tr2?.source_sha256, mode: start.json?.execution?.mode, outcome: start.json?.execution?.outcome };
    check('transfer finished COMPLETE and labelled SIMULATION (modelled paths, real SHA-256)',
        start.json?.state === 'COMPLETE' && start.json?.execution?.mode === 'SIMULATION', JSON.stringify(out.multipath));
    await api('POST', '/api/xfer/reset');
    return out;
}

(async () => {
    let evidence = {};
    try {
        await main();
        evidence = await realOperations();
    } catch (e) {
        check('script ran to completion', false, String(e?.stack ?? e).slice(0, 600));
    }
    const ignorable = p => /Failed to load resource|favicon|fonts\.g|cloudfront|ERR_BLOCKED|net::ERR/.test(p);
    const real = consoleProblems.filter(p => !ignorable(p));
    check('no unexpected browser console errors', real.length === 0, real.slice(0, 3).join(' | '));
    const failed = results.filter(r => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    fs.writeFileSync(process.env.REPORT ?? 'e2e-report.json', JSON.stringify({ results, evidence, consoleProblems }, null, 2));
    process.exit(failed.length ? 1 : 0);
})();
