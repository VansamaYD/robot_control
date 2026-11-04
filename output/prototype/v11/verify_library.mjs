// Mutating acceptance tests: run only against a dedicated service/library.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8890';
const root = path.dirname(fileURLToPath(import.meta.url));
const report = {status:'running',tests:[]};
const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH || (fs.existsSync(chrome)?chrome:undefined),args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});
let page;
async function check(name,fn){await fn();report.tests.push({name,status:'passed'});console.log('PASS',name);}
try{
 page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(base);await page.waitForFunction(()=>window.__ready);
 const bootstrap=await(await fetch(base+'/api/bootstrap')).json();
 const state=async()=>await(await fetch(base+'/api/state')).json();
 const list=async()=>await(await fetch(base+'/api/projects')).json();
 const post=async(route,data)=>{const r=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':bootstrap.token},body:JSON.stringify(data)});assert(r.ok);return r.json();};
 const initial=await state();
 if(!initial.paused)throw Error('Test instance must be paused');
 await check('protected_standard_reset_changes_only_recipe',async()=>{
  await page.click('#standard');await page.waitForFunction(()=>document.getElementById('library-message').textContent.includes('已恢复标准流程'));
  const after=await state();assert.deepEqual(after.q_rad,initial.q_rad);assert.deepEqual(after.liquid,initial.liquid);assert.equal(after.tick,initial.tick);
  assert.equal(await page.inputValue('#project'),bootstrap.default_program.revision);
 });
 let first,second,id,count;
 await check('edits_update_one_project_with_immutable_history',async()=>{
  await page.click('#edit');await page.fill('#project-name','流程库验收草稿');await page.fill('#recipe',JSON.stringify([{kind:'wait',name:'version one',duration_s:.2}]));
  await page.click('#save');await page.waitForFunction(()=>document.getElementById('source').textContent.includes('version one'));
  first=await page.inputValue('#project');const record=await(await fetch(base+'/api/projects/'+first)).json();id=record.project_id;count=(await list()).length;
  await page.click('#edit');await page.fill('#recipe',JSON.stringify([{kind:'wait',name:'version two',duration_s:.4}]));await page.click('#save');
  await page.waitForFunction(old=>document.getElementById('project').value!==old,first);second=await page.inputValue('#project');
  assert.equal((await list()).length,count);assert.equal((await(await fetch(base+'/api/projects/'+second)).json()).project_id,id);
  assert.equal((await(await fetch(base+'/api/projects/'+first)).json()).actions[0].name,'version one');
 });
 await check('history_preview_requires_restore_and_keeps_other_versions',async()=>{
  await page.selectOption('#history',first);await page.waitForFunction(()=>document.getElementById('source').textContent.includes('version one'));
  await page.waitForFunction(()=>document.getElementById('run').disabled);
  await page.click('#restore-version');await page.waitForFunction(()=>document.getElementById('library-message').textContent.includes('已恢复选中版本'));
  assert.equal((await(await fetch(base+'/api/library/history?project_id='+id)).json()).length,2);
  assert.equal((await(await fetch(base+'/api/projects/'+second)).json()).actions[0].name,'version two');
 });
 await check('archive_is_reversible_without_deleting_revisions',async()=>{
  await page.click('#archive-project');await page.waitForFunction(()=>document.getElementById('library-message').textContent.includes('已归档'));
  assert(!(await list()).some(r=>r.project_id===id));assert.equal((await fetch(base+'/api/projects/'+first)).status,200);
  await page.check('#show-archived');await page.waitForFunction(rev=>[...document.getElementById('project').options].some(o=>o.value===rev),first);
  await page.selectOption('#project',first);await page.waitForFunction(()=>document.getElementById('archive-project').textContent==='恢复到列表');
  await page.click('#archive-project');await page.waitForFunction(()=>document.getElementById('library-message').textContent.includes('已恢复到列表'));
  assert((await list()).some(r=>r.project_id===id));
 });
 await check('cleanup_preserves_builtin_recipes_and_exports_full_backup',async()=>{
  const download=page.waitForEvent('download');await page.click('#backup-library');const file=await download;
  const record=JSON.parse(fs.readFileSync(await file.path(),'utf8'));assert.equal(record.schema_version,'library-backup-1');assert(record.records.some(r=>r.revision===first));
  await page.click('#cleanup-library');await page.waitForFunction(()=>document.getElementById('library-message').textContent.includes('已备份并归档'));
  const active=await list();assert.equal(active.length,2);assert(active.every(r=>r.kind==='builtin'));assert(active.some(r=>r.action_count===24));
  const after=await state();assert.deepEqual(after.q_rad,initial.q_rad);assert.deepEqual(after.liquid,initial.liquid);
 });
 await check('three_zone_recipe_runs_and_reports_all_impacts',async()=>{
  const three=(await list()).find(r=>r.action_count===24);await page.selectOption('#project',three.revision);
  await page.waitForFunction(()=>document.getElementById('source').textContent.includes('右侧：倾倒65'));
  await post('/api/reset',{incarnation:(await state()).incarnation,confirmation:'RESET_SIMULATION'});
  await page.selectOption('#speed','8');await page.click('#run');
  await page.waitForFunction(()=>['succeeded','failed'].includes(window.__frameState?.job?.status)&&window.__frameState?.job?.program_revision===document.getElementById('project').value,null,{timeout:150000});
  const done=await state();assert.equal(done.status,'succeeded',JSON.stringify(done.job));assert.equal(done.liquid.coverage.covered_zones,3);assert.equal(done.liquid.spill_m3,0);
  assert((await page.textContent('#coverage')).includes('3/3'));
  await page.screenshot({path:path.join(root,'evidence/workbench-library-three-zone.png')});
  await post('/api/reset',{incarnation:done.incarnation,confirmation:'RESET_SIMULATION'});await page.click('#standard');
 });
 assert.deepEqual(errors,[]);report.status='passed';
}catch(error){report.status='failed';report.error=error.stack;console.error(error);process.exitCode=1;}
finally{await browser.close();fs.writeFileSync(path.join(root,'evidence/library-report.json'),JSON.stringify(report,null,2));}
