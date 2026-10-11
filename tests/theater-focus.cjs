const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const html = fs.readFileSync('site/index.html', 'utf8');
const css = fs.readFileSync('site/styles.css', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
const constant = name => code.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const theaters = [
  { id: 'ukraine', name: 'Russia–Ukraine', highlight: ['804'], camera: { lat: 48.5, lng: 34, altitude: 0.85 } },
  { id: 'mideast', name: 'Middle East', highlight: ['376'], camera: { lat: 28.5, lng: 45, altitude: 1.25 } },
  { id: 'global', name: 'Worldwide', highlight: [], listed: false }
];
const S = { theaters, region: 'world', hot: new Set(), layers: { carriers: true },
  selectedId: 'opened', selectedHull: null, selectedFlow: null, briefOpen: new Set() };
const nodes = Object.fromEntries(['#regionFocus', '#regionName', '#theaterList', '#focusToast', '#feedList'].map(id => [id, {}]));
const buttons = ['world', 'none', 'ukraine', 'mideast'].map(region => ({ dataset: { region },
  setAttribute(k, v) { this[k] = v; } }));
let mobile = false, closed = 0, filtersClosed = 0, camera = null;
const context = { S, DAY: 86400000, HOUR: 3600000, reduceMotion: false,
  $: id => nodes[id], document: { querySelectorAll: () => buttons },
  closeFly: () => {}, hotEvent: () => {},
  closeDetail: () => { closed++; S.selectedId = S.selectedHull = S.selectedFlow = null; },
  world: { pointOfView: (value, duration) => { camera = { value, duration }; } },
  isMobile: () => mobile, flyMs: () => 1200,
  toggleFilters: value => { assert.equal(value, false); filtersClosed++; },
  esc: s => String(s || ''), safeUrl: s => s, ago: () => '1h', typeLabel: e => e.type,
  TREND: { steady: ['→', 'Steady'] }, CONF_WORDS: { low: 'Low confidence' },
  briefHighlights: () => [] };
vm.createContext(context);
vm.runInContext([
  constant('selectedRegion'), constant('theaterShown'), constant('focused'), constant('FOCUS_DIM'), constant('dimOf'),
  constant('carrierOnMap'), section('  function syncRegionControls()', '  function renderCounts()'),
  section('  function briefHtml()', '  const FEED_PAGE'),
  'this.api = { selectedRegion, theaterShown, focused, dimOf, carrierOnMap, renderTheaters, syncRegionControls, selectRegion, briefHtml };'
].join('\n'), context);
const api = context.api;
api.renderTheaters();
assert(nodes['#theaterList'].innerHTML.includes('Focus on Russia–Ukraine'));
assert(!nodes['#theaterList'].innerHTML.includes('type="checkbox"'));
assert(!nodes['#theaterList'].innerHTML.includes('data-region="global"'));
assert(api.theaterShown('ukraine') && api.theaterShown('mideast') && api.theaterShown('global'));
api.selectRegion('ukraine');
assert.equal(S.region, 'ukraine'); assert.equal(closed, 1);
assert(api.theaterShown('ukraine') && !api.theaterShown('mideast') && !api.theaterShown('global'));
assert.equal(S.selectedId, null);
assert.equal(nodes['#feedList'].scrollTop, 0, 'A new region starts at the top of the list');
assert.equal(nodes['#regionName'].textContent, 'Russia–Ukraine');
assert.equal(nodes['#regionFocus'].hidden, false);
assert.equal([...S.hot].join(), '804');
assert.equal(buttons.filter(b => b['aria-pressed'] === 'true').length, 1);
assert.equal(camera.value.lat, 48.5); assert.equal(camera.value.altitude, 0.85);
assert.equal(api.dimOf(false, true), 1, 'Regional events/routes stay bright');
assert.equal(api.dimOf(false), 0.28, 'Global carrier context dims in regional focus');
S.selectedId = 'inside-region';
assert.equal(api.dimOf(false, true), 0.28, 'An opened alert keeps normal individual focus');
assert.equal(api.dimOf(true, true), 1);
S.selectedId = null;
api.selectRegion('mideast');
assert.equal(S.region, 'mideast'); assert(!api.theaterShown('ukraine'));
assert.equal([...S.hot].join(), '376');
api.renderTheaters(); assert.equal(S.region, 'mideast', 'Refresh preserves the exclusive region');
api.selectRegion('none');
assert.equal(S.region, null); assert(!api.theaterShown('ukraine') && !api.theaterShown('global'));
assert.equal(nodes['#regionFocus'].hidden, true); assert.equal(S.hot.size, 0);
assert(!api.carrierOnMap({ at_home: false }), 'Hide all removes carrier markers too');
api.renderTheaters(); assert.equal(S.region, null, 'Refresh preserves Hide all');
mobile = true; api.selectRegion('ukraine');
assert.equal(camera.value.altitude, 1.35); assert.equal(filtersClosed, 1);
api.selectRegion('world');
assert.equal(S.region, 'world'); assert(api.theaterShown('global'));
assert.equal(nodes['#regionFocus'].hidden, true); assert.equal(camera.value.altitude, 3.1);
assert(api.carrierOnMap({ at_home: false }));
const before = closed; api.selectRegion('missing'); assert.equal(closed, before); assert.equal(S.region, 'world');
context.reduceMotion = true; api.selectRegion('mideast'); assert.equal(camera.duration, 0);
S.data = { events: [], analysis: { generated_at: new Date().toISOString(), regions: theaters.slice(0, 2).map(t =>
  ({ theater: t.id, name: t.name, judgments: [{ trend: 'steady', confidence: 'low', headline: t.name, tally: {} }] })) } };
let brief = api.briefHtml(); assert(brief.includes('Middle East') && !brief.includes('Russia–Ukraine'));
api.selectRegion('none'); assert.equal(api.briefHtml(), '');
api.selectRegion('world'); brief = api.briefHtml(); assert(brief.includes('Middle East') && brief.includes('Russia–Ukraine'));
assert(html.indexOf('class="theater-section"') < html.indexOf('class="legend-section"'), 'Theaters come first');
assert(html.includes('family=Inter:wght@400;500;600;700') && css.includes('--font: "Inter"'));
assert(!css.includes('font-stretch:') && !html.includes('family=Archivo'));
assert(code.includes('else if (selectedRegion()) selectRegion("world")'), 'Escape leaves regional focus');
assert(code.includes('if (t) selectRegion(t.id)'), 'Analysis region links activate the same focus');
assert(code.includes('!selectedRegion()) S.region = "world"'), 'Removed regions safely fall back to Worldwide');
assert(!code.includes('theaterOn') && !code.includes('unlisted()'), 'No multiple-region or global bypass remains');
assert(!html.includes('x.activity'), 'Early first-load preview follows original event dates');
console.log('PASS: exclusive region/Worldwide/Hide all, camera and phone focus, brightness, alert focus, refresh, regional brief, reduced motion, control order and Inter typography.');
