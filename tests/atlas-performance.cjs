const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));
function layoutRate({phone=false, moving=false, rotating=false}) {
  let now=0, id=0, calls=0;
  const tasks=new Map();
  const schedule=(fn, delay)=>{tasks.set(++id,{at:now+delay,fn});return id;};
  const ctx={PHONE:phone,moving,controls:{autoRotate:rotating},performance:{now:()=>now},setTimeout:schedule,requestAnimationFrame:fn=>schedule(fn,16),declutter:()=>calls++};
  vm.createContext(ctx);
  vm.runInContext(section('  let declutterQueued', '  function setOffset'),ctx);
  for (now=0;now<=5000;now++) {
    if(now%16===0) ctx.queueDeclutter();
    for(const [id,t] of tasks) if(t.at<=now){tasks.delete(id);t.fn();}
  }
  return calls;
}
assert.ok(layoutRate({rotating:true})<=25, 'automatic rotation must not recluster every frame');
assert.ok(layoutRate({rotating:true,phone:true})<=20);
assert.ok(layoutRate({moving:true})<=84);
assert.ok(layoutRate({moving:true,phone:true})<=28);
assert.ok(layoutRate({})<=50, 'camera flights are bounded too');
assert.ok(layoutRate({rotating:true})>=20, 'markers continue to update during rotation');
let changes=0;
function marker(key) {
 const classes=new Set(); const count={textContent:''};
 const el={classList:{contains:k=>classes.has(k),toggle(k,on){changes++;if(on)classes.add(k);else classes.delete(k);}},querySelector:()=>count,firstChild:{title:'',getAttribute(k){return this[k];},setAttribute(k,v){this[k]=v;}},style:{setProperty(){}}};
 return {key,el,lat:0,lon:0,isEvent:true,prio:0,ev:{id:key}};
}
const S={html:[marker('a'),marker('b'),marker('c')],selectedId:null};
const ctx={S,window:{innerWidth:1440,innerHeight:900},world:{pointOfView:()=>({lat:0,lng:0,altitude:2}),getScreenCoords:()=>({x:100,y:100})},isMobile:()=>false,km:()=>0,Math};
vm.createContext(ctx);
vm.runInContext(section('  function setOffset', '  // ------------------------------------------------------------------ tooltips'),ctx);
ctx.declutter(); changes=0;ctx.declutter();
assert.equal(changes,0,'unchanged clusters must not toggle lead/hidden classes');
console.log('PASS: bounded rotation/drag/flight layout rates and zero class churn for unchanged clusters.');

// Neighbouring groups, mixed fleet markers, and edge locations must share one collision layout.
for (const phone of [false,true]) for (const altitude of [2,0.3]) {
  const width=phone?390:1440, height=844;
  const items=Array.from({length:96},(_,i)=>{
    const d=marker(`ev:${String(i).padStart(3,'0')}`);
    d.lat=width/2+(i%8)*24-85; d.lon=260+Math.floor(i/8)*12;d.prio=96-i;
    return d;
  });
  for(let i=0;i<6;i++){const d=marker(`cvn:${i}`);d.isEvent=false;d.lat=width/2;d.lon=310+i*8;items.push(d);}
  const edge=marker('ev:edge');edge.lat=width-2;edge.lon=500;items.push(edge);
  const state={html:items,selectedId:'001'};
  const env={S:state,window:{innerWidth:width,innerHeight:height},world:{pointOfView:()=>({lat:0,lng:0,altitude}),getScreenCoords:(x,y)=>({x,y})},isMobile:()=>phone,km:()=>0,Math};
  vm.createContext(env);vm.runInContext(section('  function setOffset', '  // ------------------------------------------------------------------ tooltips'),env);env.declutter();
  const shown=items.filter(d=>!d.el.classList.contains('clustered'));
  assert.ok(shown.includes(items[1]),'selected event stays individually visible');
  const eventTotal=shown.filter(d=>d.isEvent).reduce((n,d)=>n+(d.el.classList.contains('cluster-lead')?d.el._members.length:1),0);
  assert.equal(eventTotal,97,'grouping preserves every event');
  const boxes=shown.map(d=>{const w=d.el.classList.contains('cluster-lead')?(phone?76:68):46;return {l:d.lat+d._dx-w/2,r:d.lat+d._dx+w/2,t:d.lon+d._dy-23,b:d.lon+d._dy+23};});
  for(const box of boxes)assert.ok(box.l>=0&&box.r<=width,'markers remain inside screen edges');
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
    const a=boxes[i],b=boxes[j];assert.ok(!(a.l<b.r&&a.r>b.l&&a.t<b.b&&a.b>b.t),'visible marker hit targets do not overlap');
  }
}
console.log('PASS: dense groups, fleet markers, screen edges, selected events, and event counts at phone/desktop and both zoom levels.');
