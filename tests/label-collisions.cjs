// Labels and city names on the globe stay clear of symbols and of one another (a review the owner
// passed on, 2026-10-16: "Globe labels and carrier names overlap").
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));

function el() {
  const classes = new Set();
  return { classes, classList: { contains: (k) => classes.has(k), toggle(k, on) { if (on) classes.add(k); else classes.delete(k); } },
    querySelector: () => ({ textContent: '' }), firstChild: { title: '' }, style: { setProperty() {} } };
}
// screen position = (lon, lat) so each datum is placed where the test says
const at = (x, y) => ({ lat: y, lon: x });
const event = (key, x, y, prio, lw = 0) => ({ key, el: el(), ...at(x, y), isEvent: true, prio, s: 24, lw, ev: { id: key } });
const carrier = (hull, x, y) => ({ key: `cvn:${hull}`, hull, el: el(), ...at(x, y), prio: 1, cw: 120 });
const city = (name, x, y, capital = 0) => ({ key: `city:${name}`, el: el(), ...at(x, y), city: true, capital: !!capital, w: name.length * 6 + 8, hAlt: 0.004 });

const a = event('a', 100, 100, 30, 60);         // label to its right, x 117-183
const ford = carrier('CVN-78', 230, 100);       // chip x 170-290: its name would cover a's label
const b = event('b', 330, 100, 20);
const clear = city('Clearville', 230, 160);     // nothing near (below the carrier)
const under = city('Under', 120, 100, 1);       // a capital on a's label: no room on either side
const flip = city('Flipton', 300, 100);         // b's icon on its right: goes left of its dot
const S = { html: [a, ford, b, clear, under, flip], selectedId: null, selectedHull: null };
const ctx = { S, Math, world: { pointOfView: () => ({ lat: 0, lng: 0, altitude: 0.3 }), getScreenCoords: (lat, lon) => ({ x: lon, y: lat }) },
  isMobile: () => false, km: () => 0 };
vm.createContext(ctx);
vm.runInContext(section('  function setOffset', '  // ------------------------------------------------------------------ tooltips'), ctx);
ctx.declutter();

assert.ok(!a.el.classes.has('label-off'), 'the most important label keeps its place');
assert.ok(ford.el.classes.has('label-off'), "a carrier's name gives way to an event's label, leaving the icon");
assert.ok(!clear.el.classes.has('is-off') && !clear.el.classes.has('is-left'), 'a city with room shows right of its dot');
assert.ok(under.el.classes.has('is-off'), 'a capital under a label is left off, not drawn beneath it');
assert.ok(flip.el.classes.has('is-left') && !flip.el.classes.has('is-off'), 'a city with a symbol on its right goes left of its dot');

S.selectedHull = 'CVN-78';
ctx.declutter();
assert.ok(!ford.el.classes.has('label-off'), 'a selected carrier always shows its name');

// a marker the globe library hides (behind the globe) is no obstacle, whatever the estimate says
S.selectedHull = null;
b.el._vis = false;
ctx.declutter();
assert.ok(!flip.el.classes.has('is-left'), 'a hidden marker does not push a city name aside');
console.log('PASS: labels and city names clear of symbols and one another; carriers yield their names; selected carrier named; hidden markers ignored.');
