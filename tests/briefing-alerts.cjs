// Breaking alerts and city names (the owner, 2026-10-11): serious new events queue for the banner,
// most severe first, at most ALERT_MAX from one update and never twice; city names only for a
// focused region; world clocks lead with UTC.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const html = fs.readFileSync('site/index.html', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
const constant = name => code.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];

let shown = [];
const context = { reduceMotion: true, setTimeout, clearTimeout };
vm.createContext(context);
vm.runInContext([
  constant('ALERT_MS'), 'const alertQ = []; let alertNow = null;',
  'function nextAlert() { const e = alertQ.shift(); alertNow = e ? { e } : null; if (e) this.shown.push(e.id); }',
  section('  function queueAlerts(evs)', '  function alertEls()'),
  'this.api = { queueAlerts, alertQ, get now() { return alertNow; }, ALERT_MAX };',
].join('\n'), Object.assign(context, { shown }));
const { api } = context;
const ev = (id, severity, t) => ({ id, severity, _t: t });
api.queueAlerts([ev('a', 2, 1), ev('b', 3, 1), ev('c', 2, 5), ev('d', 2, 3)]);
assert.equal(api.ALERT_MAX, 3);
assert.deepEqual(shown, ['b'], 'the most severe shows first');
assert.deepEqual([...api.alertQ].map(e => e.id), ['c', 'd'], 'then the newest, capped at ALERT_MAX');
api.queueAlerts([ev('b', 3, 1), ev('c', 2, 5), ev('e', 2, 9)]);
assert.deepEqual([...api.alertQ].map(e => e.id), ['c', 'd', 'e'], 'an event already shown or waiting is not queued again');

// cities: only from the selected (listed) region
const render = section('  function renderGlobe(', '    // wave target dots');
assert(/const region = selectedRegion\(\);\s+const html = \(region && CITIES\[region\.id\] \|\| \[\]\)\.map\(cityMarker\);/.test(render));
const cities = vm.runInNewContext('(' + code.match(/const CITIES = (\{[\s\S]*?\n  \});/)[1] + ')');
for (const id of ['ukraine', 'nato_east', 'mideast', 'horn', 'drc_sahel', 'indopac', 'latam']) {
  assert(cities[id] && cities[id].length >= 10, id);
  for (const [name, lat, lon] of cities[id]) assert(Math.abs(lat) <= 90 && Math.abs(lon) <= 180, name);
}
assert(cities.ukraine.some(([n, , , cap]) => n === 'Kyiv' && cap));

// clocks: UTC first; the alert banner and preview are wired
assert(/const CLOCKS = \[\["UTC", "UTC"\]/.test(code));
assert(html.includes('id="clocks"'));
assert(code.includes('queueAlerts(fresh.filter((e) => passes(e) && e.severity >= 2));'));
assert(code.includes('params.has("alert")'));
console.log('PASS: alert queue order, cap and no repeats; cities only for a focused region; UTC clock first.');
