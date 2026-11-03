const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fragment=fs.readFileSync('/Users/vansama/.codex/visualizations/2026/11/03/01a19f63-a7c0-75c0-bf63-c3d3a5261607/arm-motion.html','utf8');
const script=fragment.match(/<script>([\s\S]*?)<\/script>/)[1];
const json=fragment.match(/<script type="application\/json"[^>]*>([\s\S]*?)<\/script>/)[1];
class E{
 constructor(){this.children=[];this.attrs={};this.listeners={};this.dataset={};this.value='';this.textContent='';}
 setAttribute(k,v){assert.ok(!String(v).includes('NaN'));this.attrs[k]=v;}
 appendChild(e){this.children.push(e);}
 replaceChildren(){this.children=[];}
 addEventListener(k,f){this.listeners[k]=f;}
}
const root=new E();root.clientWidth=736;
const selectors=['[data-scene]','[data-play]','[data-route]','[data-view]','[data-progress]','[data-phase]','[data-time]','[data-joints]'];
const nodes=Object.fromEntries(selectors.map(s=>[s,new E()]));
root.querySelector=s=>{assert.ok(nodes[s],s);return nodes[s];};
const data=new E();data.textContent=json;let nextAnimation=null;
const context={document:{getElementById:id=>id==='soy-arm-motion-data'?data:root,createElementNS:()=>new E(),createElement:()=>new E()},
 window:{addEventListener(){},openai:{setWidgetState:()=>Promise.resolve()}},ResizeObserver:class{observe(){}},
 requestAnimationFrame:f=>{nextAnimation=f;return 1;},cancelAnimationFrame:()=>{nextAnimation=null;}};
vm.runInNewContext(script,context);
const change=(s,v,event)=>{nodes[s].value=v;nodes[s].listeners[event]();};
for(const width of [320,736])for(const route of ['A','B','C'])for(const view of ['iso','side','top']){
 root.clientWidth=width;change('[data-route]',route,'change');change('[data-view]',view,'change');
 for(const t of [0,1.5,4.5,7.5,10,12,15,17.4]){
  change('[data-progress]',t,'input');assert.ok(nodes['[data-scene]'].children.length>25);
  assert.equal(root.dataset.route,route);
  const w=width,h=Number(nodes['[data-scene]'].attrs.height);
  for(const item of nodes['[data-scene]'].children)if('x'in item.attrs&&'y'in item.attrs){
   assert.ok(Number(item.attrs.x)>=0&&Number(item.attrs.x)<=w,'text x');
   assert.ok(Number(item.attrs.y)>=0&&Number(item.attrs.y)<=h,'text y');
  }
 }
}
change('[data-progress]',0,'input');nodes['[data-play]'].listeners.click();
nextAnimation(100);nextAnimation(600);
assert.ok(nodes['[data-time]'].textContent.startsWith('0.5'));
nodes['[data-play]'].listeners.click();assert.equal(nodes['[data-play]'].textContent,'播放一勺');
assert.ok(fragment.length<1024*1024);
console.log('PASS: 144 route/view/time/width draws; playback/pause; source size; finite and visible text anchors.');
