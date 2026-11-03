// Render-only diagnostic checks; never sends a motion or reset command.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright';
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || (fs.existsSync(chrome)?chrome:undefined), args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});
try {
  const page = await browser.newPage();
  await page.goto(process.env.WORKBENCH_URL || 'http://127.0.0.1:8765');
  await page.waitForFunction(()=>window.__ready);
  const text = await page.evaluate(()=>{
    const snapshot = structuredClone(window.__frameState);
    snapshot.job = {...snapshot.job, error:'RECOVERY_PATH_UNAVAILABLE', error_details:{failures:[{name:'移动并回正',code:'NO_IK'}]}};
    window.__applySnapshot(snapshot);
    const failure = document.getElementById('error').textContent;
    snapshot.job.error = 'PLANNING_TIMEOUT';
    window.__applySnapshot(snapshot);
    return {failure, timeout:document.getElementById('error').textContent};
  });
  assert(text.failure.includes('移动并回正'));
  assert(text.failure.includes('连续关节解'));
  assert(text.failure.includes('未移动'));
  assert(text.timeout.includes('时间预算'));
  console.log('PASS route failure names the rejected action and reason');
  console.log('PASS planning timeout is distinguished from no route');
} finally { await browser.close(); }
