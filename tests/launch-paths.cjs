const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
function section(from, to) {
  const start = code.indexOf(from), end = code.indexOf(to, start);
  assert(start >= 0 && end > start, `Missing section: ${from}`);
  return code.slice(start, end);
}
function harness(phone, reduceMotion = false) {
  let timerId = 0, now = 0;
  const timers = new Map(), built = [];
  const c = { PHONE: phone, reduceMotion, S: { layers: { paths: true }, selectedId: null },
    extraArcs: new Set(), pushArcs: () => {}, flashRing: () => {},
    clamp: (v, lo, hi) => Math.max(lo, Math.min(hi, v)),
    km: () => 500, dimOf: () => 1, fade: () => 1, rgba: (rgb, a) => [...rgb, a],
    CAT_RGB: { strike: [255, 90, 54] }, STATUS: { corroborated: { alpha: 1 } },
    keyed: (a, key) => { built.push(key); return a; },
    originsOf: (e) => e.origins || [], wholeCountry: (o) => !!o.whole,
    launchAreas: (e) => e.anchors || [{ lat: 10, lon: 20 }, { lat: 12, lon: 22 }],
    nearestN: (origins, d, n) => origins.slice(0, n), nearest: (origins) => origins[0],
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id) };
  vm.createContext(c);
  vm.runInContext(section('  const LAUNCHED =', '  // Long routes are drawn') +
    section('  let launchTimers =', '  // Focus: with an event') +
    '\nthis.api = { attackPaths, playLaunches, stopLaunches, launchPathCount, launchShots };', c);
  return { c, ...c.api, built, timers, tick(ms) {
    const until = now + ms;
    while (true) {
      const next = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].fn();
    }
    now = until;
  } };
}
const event = (id, launched = 1000, extra = {}) => ({ id, type: 'missile_drone', launched,
  status: 'corroborated', lat: 15, lon: 25, targets: [], ...extra });
const many = Array.from({ length: 30 }, (_, i) => event(String(i)));
let h = harness(true);
assert.equal(h.attackPaths(many).length, 24);
assert.equal(h.built.length, 24, 'Cap before creating GPU path objects');
assert.equal(h.attackPaths([many[0]]).length, 8);
h.c.S.selectedId = '29';
const focused = h.attackPaths(many);
assert.equal(focused.length, 24);
assert(focused.slice(0, 8).every((a) => a.ref.id === '29'), 'Opened event has budget first');
for (const phone of [false, true]) {
  h = harness(phone);
  const counts = [1, 25, 50, 100, 200, 1000].map((n) => h.attackPaths([event('volume', n)]).length);
  assert.deepEqual(counts, phone ? [1, 1, 2, 4, 8, 8] : [1, 1, 2, 4, 8, 40]);
  assert.equal(h.attackPaths(many).length, phone ? 24 : 180);
  const named = event('named', 1000, { origins: [{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }] });
  assert.equal(h.attackPaths([named]).length, phone ? 8 : 40);
  const targets = Array.from({ length: 16 }, (_, i) => ({ lat: 30 + i, lon: 40 + i }));
  const wave = h.attackPaths([event('wave', 1000, { wave: true, targets })]);
  assert.equal(wave.length, phone ? 8 : 40);
  assert(wave.every((a) => targets.some((t) => t.lat === a.eLat && t.lon === a.eLng)));
  if (phone) assert.deepEqual(Array.from(new Set(wave.map((a) => a.eLat))), [30, 32, 34, 36, 38, 40, 42, 44]);
  assert.equal(h.attackPaths([event('neighbor-airstrike', 1000, { type: 'airstrike' })]).length, phone ? 8 : 40);
  assert.equal(h.attackPaths([event('no-origin', 1000, { anchors: [] })]).length, 0);
  h.c.km = () => 2000;
  assert.equal(h.attackPaths([event('far-origin')]).length, 0, 'Keep nearby assumed-origin guard');
  h.c.km = () => 10;
  assert.equal(h.attackPaths([named]).length, 0, 'Skip very short paths');
  h.c.S.layers.paths = false;
  assert.equal(h.attackPaths(many).length, 0);
}
h = harness(true);
h.playLaunches(event('first'), 0); h.tick(1000);
assert.equal(h.launchShots.size, 4);
assert.equal(h.c.extraArcs.size, 4);
const unrelated = { kind: 'other' }; h.c.extraArcs.add(unrelated);
h.playLaunches(event('second'), 0);
assert.equal(h.launchShots.size, 0);
assert.equal(h.c.extraArcs.size, 1, 'Remove previous shots, preserve unrelated arcs');
h.tick(1000); assert.equal(h.launchShots.size, 4);
h.stopLaunches(); assert.equal(h.timers.size, 0); assert.equal(h.c.extraArcs.size, 1);
h.tick(10000); assert.equal(h.c.extraArcs.size, 1, 'Cancelled timers cannot restore lines');
h.playLaunches(event('finish'), 0); h.tick(10000);
assert.equal(h.launchShots.size, 0); assert.equal(h.c.extraArcs.size, 1);
h = harness(false); h.playLaunches(event('desktop'), 0);
assert.equal(h.timers.size, 32, 'Desktop barrage replay retained');
h.tick(10000); assert.equal(h.launchShots.size, 0);
h = harness(true, true); h.playLaunches(event('reduced'), 0); assert.equal(h.timers.size, 0);
h = harness(true); h.c.S.layers.paths = false; h.playLaunches(event('hidden'), 0); assert.equal(h.timers.size, 0);
assert(section('  function closeDetail()', '  // Camera moves').includes('stopLaunches();'));
assert(section('  function selectFlow(', '  function selectCarrier(').includes('stopLaunches();'));
assert(section('  function selectCarrier(', '  function renderCarrierDetail(').includes('stopLaunches();'));
assert(section('  function legendChanged()', '  function buildStaticControls()').includes('if (!S.layers.paths) stopLaunches();'));
console.log('PASS: phone 24/8/4 limits, strict desktop cap, selected priority, proportional volume, actual targets, origin guards, replay cleanup and reduced motion.');
