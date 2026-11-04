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
    const timeout = document.getElementById('error').textContent;
    snapshot.job.error = 'NO_IK';
    snapshot.job.error_details = {position_error_m:1e-10,normal_error_rad:0,joint_step_rad:.02111879013973661};
    window.__applySnapshot(snapshot);
    const legacySpeed = document.getElementById('error').textContent;
    snapshot.job.error = 'JOINT_SPEED_LIMIT';
    snapshot.job.error_details = {speed_deg_s:60.5,limit_deg_s:60};
    window.__applySnapshot(snapshot);
    const speed = document.getElementById('error').textContent;
    snapshot.job.error = null;
    snapshot.job.preplanning = true;
    snapshot.job.planning_progress = {stage:'detour',candidate:1,segment:2,segments:3,name:'测试绕行',elapsed_s:1};
    snapshot.job.detour_progress = {nodes:42};
    window.__applySnapshot(snapshot);
    return {failure, timeout, speed, legacySpeed, progress:document.getElementById('planning-info').textContent};
  });
  assert(text.failure.includes('移动并回正'));
  assert(text.failure.includes('连续关节解'));
  assert(text.failure.includes('未移动'));
  assert(text.timeout.includes('时间预算'));
  assert(text.speed.includes('60.5°/s'));
  assert(text.legacySpeed.includes('位置和方向已求解'));
  console.log('PASS speed limits and retained NO_IK records have accurate diagnostics');
  assert(text.progress.includes('绕行预检'));
  assert(text.progress.includes('搜索节点 42'));
  console.log('PASS detour stage and search nodes are visible');
  console.log('PASS route failure names the rejected action and reason');
  console.log('PASS planning timeout is distinguished from no route');
} finally { await browser.close(); }
