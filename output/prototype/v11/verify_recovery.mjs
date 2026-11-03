import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const root = path.dirname(fileURLToPath(import.meta.url));
const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8765';
const report = { status: 'running', tests: [] };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
async function check(name, fn) { const details = await fn(); report.tests.push({ name, status: 'passed', details }); console.log('PASS', name); }
async function waitFor(fn, timeout=120000) { const start = Date.now(); while (Date.now()-start<timeout) { const result=await fn(); if(result) return result; await sleep(75); } throw Error('timed out'); }
try {
  const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser=await chromium.launch({ headless:true, chromiumSandbox:true, executablePath:process.env.CHROME_PATH || (fs.existsSync(chrome)?chrome:undefined),args:['--enable-unsafe-swiftshader','--use-angle=swiftshader'] });
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const pageErrors=[]; page.on('pageerror',e=>pageErrors.push(String(e)));
  await page.goto(base);await page.waitForFunction(()=>window.__ready);
  const bootstrap=await(await fetch(base+'/api/bootstrap')).json();
  const state=async()=>(await fetch(base+'/api/state')).json();
  const post=async(route,body)=>{
    const response=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':bootstrap.token},body:JSON.stringify(body)});
    const result=await response.json(); if(!response.ok) throw Error(result.error);return result;
  };
  async function reset() {
    const before=await state();await page.click('#reset');await page.click('#reset-confirm');
    return waitFor(async()=>{const s=await state();return s.incarnation!==before.incarnation?s:null;});
  }
  async function held(page,direction=1) {
    const name=`shoulder_pan ${direction>0?'增加':'减少'} 1度`;
    const button=page.getByRole('button',{name,exact:true});await button.scrollIntoViewIfNeeded();
    const box=await button.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
    return waitFor(async()=>{const s=await state();return s.jog.active?s:null;},5000);
  }
  await check('explicit_reset_restores_pose_liquid_time_but_keeps_programs',async()=>{
    const programs=(await(await fetch(base+'/api/projects')).json()).length;
    const previous=await state();await page.click('#reset');await page.click('#reset-cancel');
    assert.equal((await state()).incarnation,previous.incarnation);
    const result=await reset();assert.equal(result.tick,0);assert.equal(result.liquid.ladle_m3,0);assert(result.scoop_readiness.ready);
    assert.equal((await(await fetch(base+'/api/projects')).json()).length,programs);
    return {incarnation:result.incarnation};
  });
  await check('stationary_cup_naturally_fills_without_a_recipe',async()=>{
    const initial=await state();assert.equal(initial.liquid.ladle_m3,0);
    await page.click('#resume');
    const filled=await waitFor(async()=>{const s=await state();return s.liquid.ladle_m3>25e-6?s:null;});
    await page.click('#pause');await page.waitForFunction(()=>window.__frameState.paused);
    assert.deepEqual(filled.q_rad,initial.q_rad);assert.equal(filled.job,null);
    await reset();return {filled_ml:filled.liquid.ladle_m3*1e6};
  });
  await page.selectOption('#speed','8');
  await check('long_hold_uses_live_jog_and_stops_on_release',async()=>{
    const initial=await state();await held(page);await sleep(650);await page.mouse.up();
    const stopped=await waitFor(async()=>{const s=await state();return !s.jog.active?s:null;});
    const delta=(stopped.q_rad[0]-initial.q_rad[0])*180/Math.PI;
    assert(delta>.5&&delta<5,delta);assert.equal(stopped.job.origin,'jog');
    await sleep(300);assert.deepEqual((await state()).q_rad,stopped.q_rad);
    return {delta_deg:delta,configured_simulation_speed:8,jog_speed_deg_s:4};
  });
  await check('blur_cancels_hold_without_a_followup_click',async()=>{
    await held(page,-1);await sleep(180);await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
    const stopped=await waitFor(async()=>{const s=await state();return !s.jog.active?s:null;});
    await page.mouse.up();await sleep(300);assert.deepEqual((await state()).q_rad,stopped.q_rad);
    assert.equal(stopped.job.end_reason,'blur');return {};
  });
  await check('no_heartbeat_expires_independently_of_page_and_speed',async()=>{
    const before=await state();const lease=await post('/api/jog/start',{joint_name:'shoulder_pan',direction:1,incarnation:before.incarnation,idempotency_key:crypto.randomUUID()});
    await sleep(650);const stopped=await state();assert(!stopped.jog.active);assert.equal(stopped.job.end_reason,'LEASE_EXPIRED');
    assert(Math.abs(stopped.q_rad[0]-before.q_rad[0])*180/Math.PI<2);
    await sleep(200);assert.deepEqual((await state()).q_rad,stopped.q_rad);
    const response=await fetch(base+'/api/jog/heartbeat',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':bootstrap.token},body:JSON.stringify({lease_token:lease.lease_token})});
    assert(!response.ok);return {lease_timeout_ms:350};
  });
  await check('closing_held_page_does_not_leave_motion_running',async()=>{
    const lost=await browser.newPage({viewport:{width:1440,height:1100}});await lost.goto(base);await lost.waitForFunction(()=>window.__ready);
    await held(lost);await lost.close();await sleep(650);const stopped=await state();assert(!stopped.jog.active);
    await sleep(200);assert.deepEqual((await state()).q_rad,stopped.q_rad);return {reason:stopped.job.end_reason};
  });
  await check('home_preserves_total_with_natural_exchange',async()=>{
    const before=await state();
    await page.click('#home');
    const complete=await waitFor(async()=>{const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));return s.job?.preset==='home'&&s.status==='succeeded'?s:null;});
    assert.equal(complete.incarnation,before.incarnation);assert(complete.scoop_readiness.ready);
    const sum=s=>['pot_m3','ladle_m3','air_m3','surface_m3','spill_m3'].reduce((v,key)=>v+s.liquid[key],0);
    assert(Math.abs(sum(complete)-sum(before))<1e-10);
    assert.equal(complete.liquid.initial_m3,before.liquid.initial_m3);return {};
  });
  const scoop=await post('/api/projects',{name:'恢复测试取汁',actions:[{kind:'scoop',name:'取汁30mL',target_m3:30e-6,duration_s:.7}]});
  const scoopJob=await post('/api/run',{program_revision:scoop.revision,idempotency_key:crypto.randomUUID()});
  await waitFor(async()=>{const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));return s.job?.job_id===scoopJob.job_id&&s.status==='succeeded'?s:null;});
  await check('loaded_ladle_parks_after_checked_return_to_pot',async()=>{
    const before=await state();assert(before.liquid.ladle_m3>29e-6);
    await page.click('#dock');
    await page.waitForFunction(()=>window.__frameState.job?.preplanning===true);
    const planning=await state();await sleep(300);assert.deepEqual((await state()).q_rad,planning.q_rad);assert.equal((await state()).tick,planning.tick);
    const parked=await waitFor(async()=>{const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));return s.status==='waiting_operator'?s:null;});
    assert(parked.parking.virtual_parked);assert(!parked.parking.support_contact_validated);assert(parked.liquid.ladle_m3<1e-9);
    assert(parked.job.dry_verified);assert(parked.job.dry_check.ladle_m3<1e-9);assert(parked.job.dry_check.air_m3<1e-9);
    assert.equal(parked.incarnation,before.incarnation);
    assert(Math.abs(parked.liquid.balance_error_m3)<1e-10);
    await page.waitForFunction(()=>window.__frameState.status==='waiting_operator');
    assert((await page.inputValue('#project')).length === 64);
    await page.screenshot({path:path.join(root,'evidence/workbench-parked.png')});
    fs.writeFileSync(path.join(root,'evidence/parked-state.json'),JSON.stringify(parked,null,2));
    return {tcp_m:parked.tcp_m,ladle_ml:parked.liquid.ladle_m3*1e6};
  });
  await check('operator_handoff_returns_from_park_to_ready_pick',async()=>{
    assert(await page.isDisabled('#run'));await page.click('#home');
    const returned=await waitFor(async()=>{const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));return s.job?.preset==='home'&&s.status==='succeeded'?s:null;});
    assert(returned.scoop_readiness.ready);return {};
  });
  await reset();
  await check('initially_empty_dock_still_drains_natural_inlet_before_exit',async()=>{
    const empty=await state();assert.equal(empty.liquid.ladle_m3,0);
    await page.click('#dock');
    const parked=await waitFor(async()=>{const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));return s.status==='waiting_operator'?s:null;});
    assert(parked.job.dry_verified);assert(parked.liquid.ladle_m3<1e-9);assert.equal(parked.liquid.spill_m3,0);
    assert(parked.job.planning_wall_s>=0);assert(parked.job.planned_duration_s>0);
    await reset();return {planning_wall_s:parked.job.planning_wall_s,planned_duration_s:parked.job.planned_duration_s,remaining_ml:parked.liquid.ladle_m3*1e6};
  });
  assert.equal(pageErrors.length,0);report.status='passed';
}catch(error){report.status='failed';report.error=error.stack;console.error(error);process.exitCode=1;}
finally{if(browser)await browser.close();fs.writeFileSync(path.join(root,'evidence/recovery-report.json'),JSON.stringify(report,null,2));}
