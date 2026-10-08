const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
function section(from, to) {
  const a = code.indexOf(from), b = code.indexOf(to, a);
  assert(a >= 0 && b > a, `Missing section ${from}`);
  return code.slice(a, b);
}
function harness(phone = false, reduced = false) {
  let now = 0, timer = 0, peak = 0;
  const timers = new Map(), seen = new Set(), starts = [];
  const c = { PHONE: phone, reduceMotion: reduced, S: { selectedFlow: null, off: new Set(), supply: { flows: [] } },
    extraArcs: new Set(), keyed: (a) => a, dimOf: () => 1, countryCenter: (cc) => ({ US: { lat: 38.9, lon: -77 }, UA: { lat: 50.45, lon: 30.5 } })[cc],
    rgba: (rgb, alpha) => [...rgb, alpha], CAT_RGB: { supply: [63, 193, 201], aid: [96, 214, 122] },
    STATUS: { corroborated: { alpha: 1 }, unconfirmed: { alpha: 0.7 }, claimed: { alpha: 0.5 } },
    setTimeout: (fn, ms) => { const id = ++timer; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    pushArcs: () => { peak = Math.max(peak, c.api.routeShots.size); for (const a of c.api.routeShots) if (!seen.has(a)) { seen.add(a); starts.push({ at: now, a }); } } };
  vm.createContext(c);
  vm.runInContext(section('  const toRad =', '  function ago(') +
    section('  function surfaceArcs(', '  // ------------------------------------------------------------------ sea lanes') +
    '\nthis.api = { supplyArcs, playRouteBurst, stopRouteBurst, routeShots };', c);
  return { c, ...c.api, timers, starts, peak: () => peak, tick(ms) {
    const end = now + ms;
    while (true) {
      const next = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      timers.delete(next[0]); now = next[1].at; next[1].fn();
    }
    now = end;
  } };
}
const flow = (key = 'supply', money = false, extra = {}) => ({ key, money, active: true,
  supplier: 'US', recipient: 'UA', from: { lat: 38.9, lon: -77 }, to: { lat: 50.45, lon: 30.5 },
  via: [{ lat: 51.5, lon: -0.1 }], deliveries: 6, modes: ['air'], status: 'corroborated', ...extra });
const open = (h, f) => { h.c.S.supply.flows = [f]; h.c.S.selectedFlow = f.key; };
const geometry = (a) => [a.sLat, a.sLng, a.eLat, a.eLng, a.alt];
for (const phone of [false, true]) for (const money of [false, true]) {
  const h = harness(phone), f = flow(money ? 'aid' : 'supply', money); open(h, f);
  const baseline = h.supplyArcs([f]);
  assert(baseline.length > 3, 'Exercise a long route with surface segments and a waypoint');
  assert(baseline.every((a) => a.kind === 'flow' && a.ms === 0));
  assert(baseline.some((a) => a.eLat === f.via[0].lat && a.eLng === f.via[0].lon));
  h.playRouteBurst(f, 1000); h.tick(899); assert.equal(h.routeShots.size, 0, 'Wait for camera');
  h.tick(1); assert.equal(h.routeShots.size, 1);
  h.tick(5000); assert.equal(h.routeShots.size, 0); assert.equal(h.timers.size, 0); assert.equal(h.c.extraArcs.size, 0);
  assert.equal(h.starts.length, baseline.length, 'One pass along every existing segment');
  assert(h.peak() <= 2, 'Only one traveling pulse, with brief segment overlap');
  assert.deepEqual(h.starts.map(({ a }) => geometry(a)), Array.from(baseline, geometry), 'Preserve exact surface curve, waypoints and direction');
  const color = money ? h.c.CAT_RGB.aid : h.c.CAT_RGB.supply;
  assert(h.starts.every(({ a }) => a.kind === 'routeShot' && a.seed === 1 && a.color[1].slice(0, 3).join() === color.join()));
  const duration = h.starts.reduce((n, { a }) => n + a.ms, 0);
  assert(Math.abs(duration - (phone ? 1600 : 2200)) < 1e-6);
  h.tick(60000); assert.equal(h.starts.length, baseline.length, 'No automatic loops');
}
let h = harness(), f = flow(); open(h, f);
const unrelated = { kind: 'other' }; h.c.extraArcs.add(unrelated);
h.playRouteBurst(f); h.tick(300); assert(h.routeShots.size);
const aid = flow('aid', true); open(h, aid); h.playRouteBurst(aid);
assert.equal(h.routeShots.size, 0); assert.equal(h.c.extraArcs.size, 1);
h.tick(400); assert(h.routeShots.size); h.stopRouteBurst();
assert.equal(h.timers.size, 0); assert.equal(h.routeShots.size, 0); assert.equal(h.c.extraArcs.size, 1);
h.tick(10000); assert.equal(h.c.extraArcs.size, 1);
for (const setup of ['reduced', 'inactive', 'pledged', 'hidden', 'no-position']) {
  h = harness(false, setup === 'reduced'); f = flow(); open(h, f);
  if (setup === 'inactive') f.active = false;
  if (setup === 'pledged') h.c.S.supply.flows = [];
  if (setup === 'hidden') h.c.S.off.add('crate');
  if (setup === 'no-position') { f.from = f.to = null; f.supplier = f.recipient = 'XX'; }
  h.playRouteBurst(f); assert.equal(h.timers.size, 0, setup);
}
h = harness(); f = flow('aid', true); open(h, f); h.c.S.off.add('crate'); h.playRouteBurst(f);
assert(h.timers.size, 'Aid works while supply routes are hidden'); h.stopRouteBurst();
h.c.S.off.add('coin'); h.playRouteBurst(f); assert.equal(h.timers.size, 0);
h = harness(); f = flow('faint', false, { from: { lat: 30, lon: 10, region: true }, status: 'claimed', modes: ['sea'] }); open(h, f);
const faint = h.supplyArcs([f]); assert(faint.every((a) => a.kind === 'flowDashed' && a.ms === 0));
h.playRouteBurst(f); h.tick(200); assert.equal([...h.routeShots][0].color[1][3], 0.3);
const part = (a, b) => section(a, b);
assert(part('  function closeDetail()', '  // Camera moves').includes('stopRouteBurst();'));
assert(part('  function select(id,', '  function renderEventDetail(').includes('stopRouteBurst();'));
assert(part('  function selectCarrier(', '  function renderCarrierDetail(').includes('stopRouteBurst();'));
assert(part('  function selectFlow(', '  function selectCarrier(').includes('if (fresh && !isPledge) playRouteBurst(f, flight);'));
assert(part('  function legendChanged()', '  function buildStaticControls()').includes('S.off.has(routeBurst.money ? "coin" : "crate")'));
assert(part('  function renderGlobe(', '  function renderControlNote()').includes('!S.supply.flows.some((f) => f.key === routeBurst.key)'));
assert(!part('  function supplyArcs(', '  // ------------------------------------------------------------------ sea lanes').includes('Math.random'));
console.log('PASS: static supply/aid routes, finite sequential bursts, exact geometry/colors, phone duration/peak, cleanup, visibility, pledges, reduced motion and no looping.');
