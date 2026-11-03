/* End-to-end spike validation, not the full product acceptance suite. */
const { createRequire } = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const requireRuntime = createRequire('/Users/vansama/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json');
const { chromium } = requireRuntime('playwright');
const root = __dirname;
const evidence = path.join(root, 'evidence'); fs.mkdirSync(evidence, { recursive: true });
const url = 'http://127.0.0.1:8765';
const report = { scope: 'trusted-code / URDF / checked motion / WebGL / exact snapshot spike',
  tests: [], browserErrors: [], limitations: ['no untrusted code sandbox', 'no liquid transfer', 'no MCP', 'no mesh collision', 'fixed scene'], samples: [] };
let browser;
async function check(name, fn) { const details = await fn(); report.tests.push({ name, status: 'passed', details }); console.log('PASS', name); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 120000) { const start = Date.now(); while (Date.now() - start < timeout) { const value = await fn(); if (value) return value; await sleep(100); } throw Error('condition timed out'); }

(async () => {
  const bootstrap = await (await fetch(url + '/api/bootstrap')).json();
  const api = async (route, body) => {
    const response = await fetch(url + route, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': bootstrap.token }, body: JSON.stringify(body) });
    const result = await response.json(); if (!response.ok) throw Error(result.error); return result;
  };
  const state = async () => (await fetch(url + '/api/state')).json();
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true, chromiumSandbox: true, args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  report.browserVersion = browser.version(); report.graphics = 'software SwiftShader for repeatable headless validation';
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  page.on('pageerror', error => report.browserErrors.push(String(error)));
  await page.goto(url); await page.waitForFunction(() => window.__ready, null, { timeout: 60000 });
  const viewState = async () => page.evaluate(() => window.__frameState);
  await check('official_meshes_and_paused_start', async () => {
    const s = await viewState(); assert(s.paused); assert.equal(s.tick, 0); assert(s.visual_mesh_count >= 15);
    assert(s.tcp_error_m < 1e-10); assert(s.maximum_link_matrix_error < 1e-10);
    return { meshes: s.visual_mesh_count, tcp_error_m: s.tcp_error_m, matrix_error: s.maximum_link_matrix_error };
  });
  await page.screenshot({ path: path.join(evidence, 'workbench-initial.png') });
  await page.selectOption('#speed', '8');
  await page.click('#run');
  await until(async () => { const s = await state(); if (s.status === 'failed') throw Error(JSON.stringify(s.job)); return s.tick >= 20; });
  await page.click('#pause');
  const paused = await state(); await sleep(250);
  await check('pause_freezes_authoritative_state', async () => {
    const after = await state(); assert(after.paused); assert.equal(after.tick, paused.tick); assert.deepEqual(after.q_rad, paused.q_rad);
    return { tick: after.tick };
  });
  await page.click('#step');
  await check('single_tick_advances_exactly_20ms', async () => {
    const after = await state(); assert.equal(after.tick, paused.tick + 1); assert(after.paused);
    assert(Math.abs(after.simulation_time_s - paused.simulation_time_s - .02) < 1e-10); return { tick: after.tick };
  });
  await page.click('#resume');
  const boot = (await state()).incarnation;
  let lastTick = -1;
  const sampleTimer = setInterval(async () => {
    try {
      const s = await viewState();
      if (s.tick !== lastTick) { lastTick = s.tick; report.samples.push({ tick: s.tick, revision: s.state_revision,
        action: s.action?.name, line: s.action?.source_line, tcp_error_m: s.tcp_error_m,
        matrix_error: s.maximum_link_matrix_error }); }
    } catch (_) {}
  }, 120);
  const middle = await until(async () => { const s = await state(); if (s.tick > 1000) return s; });
  await page.screenshot({ path: path.join(evidence, 'workbench-running.png') });
  const finished = await until(async () => { const s = await state(); if (['succeeded', 'failed'].includes(s.status)) return s; }, 180000);
  clearInterval(sampleTimer);
  await check('six_code_actions_complete_with_consistent_frames', async () => {
    assert.equal(finished.status, 'succeeded', JSON.stringify(finished.job));
    assert.equal(finished.tick, 2270); assert.equal(finished.incarnation, boot);
    assert(report.samples.length > 5);
    assert(report.samples.every(s => s.tcp_error_m < 1e-10 && s.matrix_error < 1e-10));
    const events = await (await fetch(url + '/api/events')).json();
    assert.equal(events.filter(event => event.event === 'action_started').length, 6);
    assert.equal(events.filter(event => event.event === 'action_completed').length, 6);
    const hash = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, 'flow.py'))).digest('hex');
    assert.equal(hash, finished.code_hash);
    fs.writeFileSync(path.join(evidence, 'events.json'), JSON.stringify(events, null, 2));
    return { tick: finished.tick, simulation_time_s: finished.simulation_time_s, sampled_frames: report.samples.length, code_hash: hash };
  });
  await check('idempotency_and_cancel_during_planning', async () => {
    const request = { idempotency_key: 'cancel-' + Date.now() };
    const first = await api('/api/run', request); const retry = await api('/api/run', request);
    assert.equal(first.job_id, retry.job_id);
    await api('/api/control', { command: 'cancel' });
    const cancelled = await state(); await sleep(1500); const after = await state();
    assert.equal(after.status, 'cancelled'); assert.equal(after.tick, cancelled.tick); assert.deepEqual(after.q_rad, cancelled.q_rad);
    return { job_id: first.job_id, tick: after.tick };
  });
  // Close every interactive page before creating a new read-only renderer.
  await page.close();
  const beforeCapture = await state();
  const capture = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  capture.on('pageerror', error => report.browserErrors.push(String(error)));
  await capture.goto(url + '/?capture=1&snapshot=' + middle.state_revision);
  await capture.waitForFunction(() => window.__ready, null, { timeout: 60000 });
  await check('headless_exact_historical_snapshot_without_interactive_ui', async () => {
    const frame = await capture.evaluate(() => window.__frameState);
    assert.equal(frame.state_revision, middle.state_revision); assert.equal(frame.tick, middle.tick);
    assert.deepEqual(frame.q_rad, middle.q_rad); assert(frame.tcp_error_m < 1e-10);
    assert.notEqual(frame.state_revision, beforeCapture.state_revision);
    await capture.screenshot({ path: path.join(evidence, 'snapshot.png') });
    const after = await state(); assert.equal(after.tick, beforeCapture.tick); assert.deepEqual(after.q_rad, beforeCapture.q_rad);
    fs.writeFileSync(path.join(evidence, 'snapshot-metadata.json'), JSON.stringify(frame, null, 2));
    const png = fs.readFileSync(path.join(evidence, 'snapshot.png'));
    return { revision: frame.state_revision, tick: frame.tick, image_sha256: crypto.createHash('sha256').update(png).digest('hex'), bytes: png.length };
  });
  await capture.close();
  await check('autonomous_execution_with_all_browser_pages_closed', async () => {
    assert.equal(browser.contexts().flatMap(context => context.pages()).length, 0);
    const before = await state();
    await api('/api/run', { idempotency_key: 'headless-' + Date.now() });
    const progressed = await until(async () => {
      const current = await state();
      if (current.status === 'failed') throw Error(JSON.stringify(current.job));
      return current.tick > before.tick + 5 ? current : null;
    });
    await api('/api/control', { command: 'cancel' });
    return { before_tick: before.tick, progressed_tick: progressed.tick, interactive_pages: 0 };
  });
  await check('unknown_snapshot_and_unauthorized_write_rejected', async () => {
    const expired = await fetch(url + '/api/state?revision=999999'); assert.equal(expired.status, 400);
    assert.equal((await expired.json()).error, 'SNAPSHOT_EXPIRED');
    const unauth = await fetch(url + '/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"idempotency_key":"bad"}' });
    assert.equal(unauth.status, 403); return {};
  });
  await check('malformed_json_shape_and_static_path_escape_rejected', async () => {
    const invalid = await fetch(url + '/api/run', { method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-Session-Token': bootstrap.token }, body: '[]' });
    assert.equal(invalid.status, 409);
    assert.equal((await invalid.json()).error, 'INVALID_ARGUMENT');
    const secret = await fetch(url + '/engine.py'); assert.equal(secret.status, 404);
    return {};
  });
  assert.equal(report.browserErrors.length, 0);
  report.status = 'passed';
})().catch(error => { report.status = 'failed'; report.error = error.stack; console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); fs.writeFileSync(path.join(evidence, 'browser-report.json'), JSON.stringify(report, null, 2)); });
