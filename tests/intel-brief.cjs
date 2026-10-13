// The intelligence brief (the owner, 2026-10-13): a click on a country opens its brief; the lookup
// must work across the 180th meridian (Russia) and count Crimea, drawn inside Russia, as Ukraine.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const html = fs.readFileSync('site/index.html', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
const constant = name => code.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('site/vendor/topojson-client.min.js', 'utf8'), ctx);
const topo = JSON.parse(fs.readFileSync('site/assets/countries-110m.json', 'utf8'));
ctx.landShapes = ctx.topojson.feature(topo, topo.objects.countries).features.filter(f => f.geometry)
  .map(f => ({ ...f, geometry: { type: 'MultiPolygon', coordinates: f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates } }));  // as app.js sanitize() leaves them
vm.runInContext([
  constant('ISO_NUM'), 'const ctlBoxes = new Map();',
  section('  function polyBox(poly)', '  function controlAt('),
  section('  const ISO_ALPHA', '  const plural ='),
  'this.countryAt = countryAt;',
].join('\n'), ctx);
assert.equal(ctx.countryAt(65.06, 103.25), 'RU', 'Siberia: Russia crosses the 180th meridian');
assert.equal(ctx.countryAt(66.0, -175.0), 'RU', 'Chukotka, west of the meridian');
assert.equal(ctx.countryAt(50.45, 30.52), 'UA');
assert.equal(ctx.countryAt(45.0, 34.1), 'UA', 'Crimea is Ukraine');
assert.equal(ctx.countryAt(24.7, 46.7), 'SA');
assert.equal(ctx.countryAt(30.0, -40.0), null, 'open sea');
// the brief replaces the list and a click away closes it
assert(code.includes('openBrief({ iso })') && code.includes('if (S.brief) closeDetail()'));
assert(code.includes('(focused() || S.brief) && !world.toGlobeCoords'), 'a click off the globe closes it');
assert(code.includes('["Moscow", "Europe/Moscow"]') && !code.includes('Asia/Riyadh') && code.includes('["Tehran", "Asia/Tehran"]') && !code.includes('Europe/Kyiv'), 'Moscow clock, not Riyadh');
assert(html.includes('Click a country for its brief'));
console.log('PASS: country lookup (180th meridian, Crimea, sea), brief opens and closes, Moscow clock.');
