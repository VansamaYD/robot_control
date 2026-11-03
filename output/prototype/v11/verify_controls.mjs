import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.dirname(fileURLToPath(import.meta.url));
const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8765';
const report = { tests: [], status: 'running' };
let browser;
try {
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser = await chromium.launch({ headless: true, chromiumSandbox: true, executablePath: process.env.CHROME_PATH || (fs.existsSync(chrome) ? chrome : undefined), args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = []; page.on('pageerror', e => errors.push(String(e)));
  await page.goto(base); await page.waitForFunction(() => window.__ready);
  await page.selectOption('#speed', '1');
  const initial = await page.evaluate(() => window.__frameState.q_rad[0]);
  await page.getByRole('button', { name: 'shoulder_pan 增加 1度', exact: true }).click();
  await page.waitForFunction(() => window.__frameState.job?.origin === 'manual' && window.__frameState.action?.complete === false && window.__frameState.status === 'running');
  await page.click('#pause');
  await page.waitForFunction(() => window.__frameState.paused);
  const paused = await page.evaluate(() => window.__frameState);
  assert(paused.paused); assert(Math.abs(paused.target_q_rad[0] - initial - Math.PI/180) < 1e-9);
  assert(paused.velocity_rad_s.every(value => value === 0));
  report.tests.push({ name: 'manual_joint_step_and_goal_telemetry', status: 'passed' });
  await page.click('#snapshot');
  await page.waitForFunction(() => !document.getElementById('snapshot-preview').hidden, null, { timeout: 40000 });
  assert((await page.getAttribute('#snapshot-preview', 'src')).startsWith('data:image/png;base64,'));
  report.tests.push({ name: 'ui_pinned_snapshot_preview_and_download', status: 'passed' });
  await page.screenshot({ path: path.join(root, 'evidence/workbench-controls.png') });
  await page.click('#resume');
  await page.waitForFunction(() => window.__frameState.job?.origin === 'manual' && window.__frameState.status === 'succeeded');
  const final = await page.evaluate(() => window.__frameState);
  assert(Math.abs(final.q_rad[0] - initial - Math.PI/180) < 1e-9);
  assert.equal(errors.length, 0); assert.equal((await page.evaluate(() => window.__errors)).length, 0);
  report.tests.push({ name: 'manual_resume_reaches_checked_target', status: 'passed' });
  report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = error.stack; console.error(error); process.exitCode = 1; }
finally { if (browser) await browser.close(); fs.writeFileSync(path.join(root, 'evidence/controls-report.json'), JSON.stringify(report, null, 2)); }
console.log(JSON.stringify(report));
