// Dedicated service only: verify real surface motion and return packets.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const base=process.env.WORKBENCH_URL || 'http://127.0.0.1:8891';
const report={status:'running',tests:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const bootstrap=await(await fetch(base+'/api/bootstrap')).json();
const state=async()=>await(await fetch(base+'/api/state')).json();
async function post(route,data){const r=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':bootstrap.token},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw Error(result.error);return result;}
async function until(fn,timeout=120000){const start=Date.now();while(Date.now()-start<timeout){const s=await state();if(s.status==='failed')throw Error(JSON.stringify(s.job));if(fn(s))return s;await sleep(40);}throw Error('timeout');}
try{
 let s=await state();await post('/api/reset',{incarnation:s.incarnation,confirmation:'RESET_SIMULATION'});
 const initial=await state();assert.equal(initial.configuration_revision,'S_RACK-geometry-3');assert(initial.liquid.initial_m3>.0039);
 assert.equal(bootstrap.scene.chicken.center_m[2]-bootstrap.scene.chicken.radii_m[2],.095);
 assert(bootstrap.scene.rack.rods.length>10);report.tests.push({name:'rack_geometry_above_80mm_liquid',status:'passed'});
 await post('/api/speed',{speed:8});const job=await post('/api/run',{program_revision:bootstrap.default_program.revision,idempotency_key:'rack-'+Date.now()});
 await until(s=>s.action?.name==='倾斜汤勺 65°');await post('/api/speed',{speed:1});
 s=await until(s=>s.liquid.surface_packets?.length>3&&s.liquid.packets.some(p=>p.kind==='return'));
 await post('/api/control',{command:'pause',job_id:job.job_id});const frozen=await state();
 assert(frozen.liquid.surface_packets.length>0);assert(frozen.liquid.return_air_m3>0);report.tests.push({name:'surface_flow_and_airborne_return_exist_together',status:'passed'});
 const image=await post('/api/images',{state_revision:frozen.state_revision,incarnation:frozen.incarnation,width:1280,height:900,view:'rack'});
 const png=Buffer.from(image.data,'base64');assert(png.length>10000);assert.equal(png.subarray(1,4).toString(),'PNG');
 fs.writeFileSync(path.join(root,'evidence/rack-visible-runoff.png'),png);
 fs.writeFileSync(path.join(root,'evidence/rack-runoff-state.json'),JSON.stringify({state_revision:frozen.state_revision,tcp_m:frozen.tcp_m,normal_world:frozen.normal_world,liquid:frozen.liquid},null,2));
 const after=await state();assert.equal(after.tick,frozen.tick);assert.deepEqual(after.liquid,frozen.liquid);report.tests.push({name:'pinned_rack_view_renders_without_advancing_fluid',status:'passed'});
 await post('/api/speed',{speed:8});await post('/api/control',{command:'resume',job_id:job.job_id});
 s=await until(s=>s.status==='succeeded');assert(s.liquid.returned_m3>25e-6);assert.equal(s.liquid.spill_m3,0);assert(Math.abs(s.liquid.balance_error_m3)<1e-12);
 report.tests.push({name:'unchanged_standard_completes_and_recovers_liquid_without_spill',status:'passed',returned_ml:s.liquid.returned_m3*1e6});
 await post('/api/reset',{incarnation:s.incarnation,confirmation:'RESET_SIMULATION'});report.status='passed';
 console.log(report.tests.map(t=>'PASS '+t.name).join('\n'));
}catch(error){report.status='failed';report.error=error.stack;console.error(error);process.exitCode=1;}
finally{fs.writeFileSync(path.join(root,'evidence/rack-browser-report.json'),JSON.stringify(report,null,2));}
