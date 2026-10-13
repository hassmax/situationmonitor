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
const context = { reduceMotion: true, setTimeout, clearTimeout, HOUR: 3600e3,
  catOf: e => [e.cat || 'strike'], km: (a, b, c, d) => Math.hypot(a - c, b - d) * 111 };
vm.createContext(context);
vm.runInContext([
  constant('ALERT_MS'), 'const alertQ = []; let alertNow = null;',
  'function nextAlert() { const e = alertQ.shift(); alertNow = e ? { e } : null; if (e) this.shown.push(e.id); }',
  section('  const ALERT_QUIET', '  function alertEls()'),
  'this.api = { queueAlerts, alertQ, get now() { return alertNow; }, ALERT_MAX };',
].join('\n'), Object.assign(context, { shown }));
const { api } = context;
const ev = (id, severity, t, lat = t, cat = 'strike') => ({ id, severity, _t: t, lat, lon: 0, cat });
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
assert(code.includes('if (cleared.length) queueAlerts(cleared);'));
// only events the gatekeeper checked (pipeline/gate.py) raise banners: new and checked, or checked since
assert(/const cleared = data\.events\.filter\(\(e\) => !e\.unchecked && \(!before\.has\(e\.id\) \|\| before\.get\(e\.id\)\)/.test(code));
assert(code.includes('passes(e) && !e.unchecked && e.severity >= 2'));
assert(code.includes('params.has("alert")'));
// one incident arriving as several events, at one spot, alerts once (2026-10-12: Koya came as four)
api.alertQ.length = 0;
api.queueAlerts([ev('k1', 3, 50, 36), ev('k2', 3, 51, 36.1), ev('k3', 2, 52, 36.05, 'strike')]);
const got = ['k1', 'k2', 'k3'].filter(id => shown.includes(id) || [...api.alertQ].some(e => e.id === id));
assert.deepEqual(got, ['k2'], 'one alert for the incident: the most serious, newest copy');
api.queueAlerts([ev('k4', 3, 60, 36.02)]);
assert.ok(![...api.alertQ].some(e => e.id === 'k4') && !shown.includes('k4'), 'a later copy within the quiet hours is not alerted');
api.queueAlerts([ev('n1', 3, 61, 36.02, 'naval')]);
assert.ok(shown.includes('n1') || [...api.alertQ].some(e => e.id === 'n1'), 'another kind of event at the spot is');
console.log('PASS: alert queue order, cap and no repeats; cities only for a focused region; UTC clock first; one alert per incident nearby.');
