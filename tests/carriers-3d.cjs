// 3D carriers (the owner, 2026-10-17): the bow points to a reported destination, else along the last
// reported move, else north; carriers reported at one spot stand side by side, bows north, names stacked.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));

const ctx = { Math, PHONE: false, toRad: (d) => (d * Math.PI) / 180 };
ctx.km = (a1, o1, a2, o2) => {  // haversine, as app.js
  const r = ctx.toRad, h = Math.sin(r(a2 - a1) / 2) ** 2 + Math.cos(r(a1)) * Math.cos(r(a2)) * Math.sin(r(o2 - o1) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
};
vm.createContext(ctx);
vm.runInContext(section('  const SHIP_PX', '  function shipModel'), ctx);
vm.runInContext(section('  // degrees clockwise from north', '  // one size on screen'), ctx);

const near = (a, b, tol = 2) => Math.abs(a - b) <= tol;
const east = { lat: 0, lon: 60, heading_to: { lat: 0, lon: 80 } };
assert.ok(near(ctx.shipBearing(east), 90), 'bow toward the reported destination (east)');
const moved = { lat: 10, lon: 60, prev: { lat: 20, lon: 60 } };
assert.ok(near(Math.abs(ctx.shipBearing(moved)), 180), 'no destination: along the last reported move (south)');
assert.equal(ctx.shipBearing({ lat: 10, lon: 60 }), 0, 'nothing reported: north');

const a = { hull: 'A', lat: 16, lon: 63, heading_to: { lat: 20, lon: 90 } }, b = { hull: 'B', lat: 16, lon: 63 }, c = { hull: 'C', lat: 35, lon: -70 };
ctx.formation([a, b, c]);
assert.deepEqual([a._side, b._side, c._side], [-0.5, 0.5, null], 'two at one spot stand either side of it; a lone one in place');
assert.deepEqual([a._stack, b._stack, c._stack], [0, 1, 0], 'their names stack');
assert.equal(ctx.shipBearing(a), 0, 'side by side, bows north, whatever one was heading to');
const ea = ctx.shipExtent(a), eb = ctx.shipExtent(b);
const [SEP, PX] = vm.runInContext('[SHIP_SEP, SHIP_PX]', ctx);  // consts, not properties of the context
assert.ok(ea.dx < 0 && eb.dx > 0 && near(eb.dx - ea.dx, SEP * PX, 0.01), 'a ship width apart on screen');
assert.ok(near(ea.hy, PX / 2, 0.01), 'bow north: tall on screen, so its name goes below the stern');
console.log('PASS: 3D carriers point to a reported destination, else along the last move, else north; ships at one spot stand side by side, names stacked.');
