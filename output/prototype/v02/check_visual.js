// Exercise the actual fragment script with a tiny DOM, then compare its model
// against the independently implemented Python calculation.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const path='/Users/vansama/.codex/visualizations/2026/11/03/01a19f63-a7c0-75c0-bf63-c3d3a5261607/ladle-clearance.html';
const fragment=fs.readFileSync(path,'utf8');
const script=fragment.match(/<script>([\s\S]*?)<\/script>/)[1];
class E {
  constructor(){this.children=[];this.attrs={};this.listeners={};this.dataset={};this.textContent='';}
  setAttribute(k,v){this.attrs[k]=v;}
  appendChild(e){this.children.push(e);}
  replaceChildren(){this.children=[];}
  addEventListener(k,f){this.listeners[k]=f;}
}
const root=new E();root.clientWidth=736;
const fields={pot:new E(),spoon:new E(),level:new E(),offset:new E()};
Object.assign(fields.level,{min:40,max:100,value:80});
Object.assign(fields.offset,{min:0,max:50,value:35});
const parts={'[data-view="top"]':new E(),'[data-view="side"]':new E(),'[data-result]':new E(),
  '[data-value="level"]':new E(),'[data-value="offset"]':new E()};
for(const k in fields)parts[`[data-field="${k}"]`]=fields[k];
root.querySelector=s=>{assert.ok(parts[s],s);return parts[s];};
const context={document:{getElementById:()=>root,createElementNS:()=>new E()},
  window:{addEventListener(){},openai:{setWidgetState:()=>Promise.resolve()}},ResizeObserver:class{observe(){}}};
vm.runInNewContext(script,context);
const results=JSON.parse(fs.readFileSync(__dirname+'/geometry_results.json','utf8')).scenarios;
for(const s of results){
  for(const [k,v]of Object.entries({pot:s.diameter,spoon:s.ladle_diameter,level:s.liquid_height,offset:s.chicken_offset_y})){
    fields[k].value=v;fields[k].listeners.input();
  }
  assert.equal(root.dataset.feasible,String(s.feasible),s.name);
  assert.ok(Math.abs(Number(root.dataset.margin)-s.best_extra_clearance_mm)<=.011,s.name);
}
root.clientWidth=320;fields.level.listeners.input();
assert.ok(parts['[data-view="top"]'].children.length>3);
assert.ok(parts['[data-view="side"]'].children.length>3);
assert.ok(!fragment.includes('\\"'));
console.log('Passed: 9 Python/JavaScript geometry comparisons; 320px draw; all controls.');
