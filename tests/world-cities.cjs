// City names: a region names its own cities; Worldwide and a focused country name the cities in the
// countries where the shown events happened (the owner, 2026-10-15).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
const constant = name => code.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const theaters = [{ id: 'ukraine', name: 'Russia–Ukraine' }, { id: 'mideast', name: 'Middle East' }];
const S = { theaters, region: 'world', country: null };
const where = { Kyiv: 'UA', Kharkiv: 'UA', Moscow: 'RU', Riyadh: 'SA', Tehran: 'IR' };
const context = { S, landShapes: [{}], countryAt: (lat, lon) => Object.entries(where).find(([n]) => context.coords[n][0] === lat && context.coords[n][1] === lon)?.[1] || null };
vm.createContext(context);
vm.runInContext([
  section('  const CITIES = {', '  const FALLBACK_THEATERS'), constant('selectedRegion'),
  section('  let cityCountry = null;', '  // A city name on the globe'),
  'this.coords = Object.fromEntries(Object.values(CITIES).flat().map(c => [c[0], [c[1], c[2]]]));',
  'this.api = { citiesFor, CITIES };'
].join('\n'), context);
const { citiesFor, CITIES } = context.api;
const names = (list) => list.map(c => c[0]).sort();
const events = [{ country: 'UA' }, { country: 'IR' }, { country: null }];
assert.deepEqual(names(citiesFor(events)), ['Kharkiv', 'Kyiv', 'Tehran'], 'Worldwide names the cities of countries with events');
assert.deepEqual(citiesFor([]), [], 'No events, no names');
S.region = 'mideast';
assert.equal(citiesFor(events).length, CITIES.mideast.length, 'A region names all its own cities');
S.country = 'SA';
assert.deepEqual(names(citiesFor([{ country: 'SA' }])), ['Riyadh'], 'A focused country names its cities');
S.country = null; S.region = null;
assert.deepEqual(citiesFor(events), [], 'Hide all names nothing');
context.landShapes = [];
S.region = 'world';
assert.deepEqual(citiesFor(events), [], 'Before the land shapes load, nothing is looked up');
console.log('PASS: city names in Worldwide follow the countries with events; regions, country focus and Hide all.');
