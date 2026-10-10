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
 const el={classList:{contains:k=>classes.has(k),toggle(k,on){changes++;if(on)classes.add(k);else classes.delete(k);}},querySelector:()=>count,firstChild:{title:''},style:{setProperty(){}}};
 return {key,el,lat:0,lon:0,isEvent:true,prio:0,ev:{id:key}};
}
const S={html:[marker('a'),marker('b'),marker('c')],selectedId:null};
const ctx={S,world:{pointOfView:()=>({lat:0,lng:0,altitude:2}),getScreenCoords:()=>({x:100,y:100})},isMobile:()=>false,km:()=>0,Math};
vm.createContext(ctx);
vm.runInContext(section('  function setOffset', '  // ------------------------------------------------------------------ tooltips'),ctx);
ctx.declutter(); changes=0;ctx.declutter();
assert.equal(changes,0,'unchanged clusters must not toggle lead/hidden classes');
console.log('PASS: bounded rotation/drag/flight layout rates and zero class churn for unchanged clusters.');
