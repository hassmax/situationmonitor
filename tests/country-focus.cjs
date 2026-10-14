// Country focus: clicking a country keeps only the events involving it, like a regional focus.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const html = fs.readFileSync('site/index.html', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
const constant = name => code.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const theaters = [
  { id: 'ukraine', name: 'Russia–Ukraine', highlight: ['804'], camera: { lat: 48.5, lng: 34, altitude: 0.85 } },
  { id: 'mideast', name: 'Middle East', highlight: ['376'], camera: { lat: 28.5, lng: 45, altitude: 1.25 } }
];
const S = { theaters, region: 'ukraine', country: null, hot: new Set(), layers: { carriers: true }, brief: null,
  windowH: 24, statusOn: new Set(['corroborated']), off: new Set(), query: '', data: { control: [], frontline: { areas: [] } } };
const nodes = Object.fromEntries(['#regionFocus', '#regionKicker', '#regionName', '#regionBack', '#feedList']
  .map(id => [id, { setAttribute(k, v) { this[k] = v; } }]));
let camera = null, renders = 0, closed = 0;
const square = (lon, lat, size) => [[[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]];
const context = { S, DAY: 86400000, HOUR: 3600000, reduceMotion: false, Date,
  $: id => nodes[id], document: { querySelectorAll: () => [] },
  ISO_NUM: new Map([['QA', '634'], ['IR', '364'], ['UA', '804']]),
  REP_POINT: { QA: { lat: 25.3, lon: 51.2 }, IR: { lat: 32, lon: 53 } },
  centers: new Map(), CONTROL_THEATER: {},
  landShapes: [{ id: '634', geometry: { coordinates: [square(50.7, 24.5, 1.5)] } }, { id: '364', geometry: { coordinates: [square(44, 25, 19)] } }],
  countryName: c => ({ QA: 'Qatar', IR: 'Iran', UA: 'Ukraine' }[c] || c),
  esc: s => String(s ?? ''), world: { pointOfView: (value, duration) => { camera = { value, duration }; } },
  isMobile: () => false, flyMs: () => 1000, render: () => { renders++; }, closeDetail: () => { closed++; S.brief = null; },
  legendKey: e => e.type, matches: () => true };
vm.createContext(context);
vm.runInContext([
  'const toRad = (d) => (d * Math.PI) / 180;', 'const clamp = (x, a, b) => Math.max(a, Math.min(b, x));',
  section('  function km(', '  const hugAlt'), section('  function ringKm2(', '  function paintControl('),
  section('  const countryCenter =', '  const landColor'), section('  function controlInRegion(', '  const controlLayers'),
  constant('selectedRegion'), constant('theaterShown'), section('  // A focused country keeps', '  // Every time window'),
  section('  function passes(', '  const visibleEvents'), constant('carrierOnMap'),
  section('  function syncRegionControls()', '  function renderTheaters()'),
  section('  // Country focus: like a region', '  function renderCounts()'), section('  function briefTeaser()', '  function openBrief('),
  'this.api = { involves, inScope, passes, carrierOnMap, controlInRegion, syncRegionControls, focusCountry, leaveCountry, briefTeaser };'
].join('\n'), context);
const api = context.api;
const now = Date.now();
const ev = (o) => ({ _t: now - 3600e3, status: 'corroborated', type: 'airstrike', theater: 'mideast', parties: [], ...o });
const here = ev({ country: 'QA' }), byQatar = ev({ country: 'IR', attacker: 'QA' }), talks = ev({ country: 'US', theater: 'global', parties: ['QA', 'IR'] }),
  arms = ev({ country: 'QA', type: 'arms_transfer', transfer: { supplier: 'US', recipient: 'QA' } }), other = ev({ country: 'IL', attacker: 'IR' }),
  kyiv = ev({ country: 'UA', theater: 'ukraine' });

api.syncRegionControls();
assert(api.passes(kyiv) && !api.passes(here), 'Without a country, the region decides');
api.focusCountry('QA');
assert.equal(S.country, 'QA');
for (const e of [here, byQatar, talks, arms]) assert(api.passes(e), 'Events involving the country show, in any region');
for (const e of [other, kyiv]) assert(!api.passes(e), 'Events not involving it are hidden');
assert.equal([...S.hot].join(), '634', 'Only the country is highlighted');
assert.equal(nodes['#regionFocus'].hidden, false);
assert.equal(nodes['#regionKicker'].textContent, 'Country focus');
assert.equal(nodes['#regionName'].textContent, 'Qatar');
assert.equal(nodes['#regionBack']['aria-label'], 'Return to Russia–Ukraine', 'The bar returns to the region underneath');
assert.equal(camera.value.lat, 25.3); assert.equal(camera.value.lng, 51.2);
assert.equal(camera.value.altitude, 0.4, 'A small country is shown close up');
assert(api.briefTeaser().includes('data-brief="QA"') && api.briefTeaser().includes('Qatar'));
assert(api.carrierOnMap({ at_home: false, _lat: 26, _lon: 54 }), 'Carriers within 1,500 km stay');
assert(!api.carrierOnMap({ at_home: false, _lat: 10, _lon: 120 }), 'Distant carriers are hidden');
assert(api.controlInRegion({ country: 'QA' }) && !api.controlInRegion({ country: 'UA', conflict: 'ukraine' }));
api.focusCountry('IR');
assert(camera.value.altitude > 1, 'A large country is shown from further out');
assert(api.passes(other) && api.passes(byQatar) && !api.passes(here));
S.brief = { iso: 'IR' };
api.leaveCountry();
assert.equal(S.country, null); assert.equal(closed, 1, 'Leaving closes the country brief');
assert.equal(camera.value.lat, 48.5, 'Leaving returns the camera to the region');
assert.equal(nodes['#regionKicker'].textContent, 'Region focus');
assert(api.passes(kyiv) && !api.passes(here));
const r = renders; api.leaveCountry(); assert.equal(renders, r, 'Leaving twice does nothing');

assert(code.includes('if (target.iso && !refresh) focusCountry(target.iso);'), 'Opening a country brief focuses the country');
assert(code.includes('else if (S.country) leaveCountry();'), 'Escape leaves the country focus');
assert(/S\.region = region;\n\s+S\.country = null;/.test(code), 'Choosing a region leaves the country');
assert(html.includes('id="regionKicker"') && html.includes('id="regionBack"'));
console.log('PASS: country focus filter (place, attacker, parties, transfers), highlight, bar, camera, carriers, control, teaser, leaving.');
