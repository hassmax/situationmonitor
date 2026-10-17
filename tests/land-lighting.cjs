// Lit countries come from a table of colours, not a repaint of the land picture (the owner asked for
// speed, 2026-10-17): entry 0 is plain land, a lit country's id holds its colour, others stay clear.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));

let puts = 0;
const row = { data: new Uint8ClampedArray(1024 * 4) };
const ctx = {
  S: { briefNum: 643, active: new Set(['804']), hot: new Set(['804', '112', '643']) },
  landShapes: [{ id: '804' }, { id: '643' }, { id: '112' }, { id: '250' }, { id: undefined }],
  landIds: { key: '', states: { needsUpdate: false }, ctx: { createImageData: () => row, putImageData: () => { puts++; } } },
  hexRgb: (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)),
};
vm.createContext(ctx);
vm.runInContext(section('  const LAND_BASE', '  const OCEAN'), ctx);
vm.runInContext(section('  function paintStates', '  function paintLand'), ctx);
ctx.paintStates();

const at = (n) => [...row.data.slice(n * 4, n * 4 + 4)];
assert.deepEqual(at(0), [0x14, 0x1b, 0x24, 255], 'entry 0 is the plain land the picture holds');
assert.deepEqual(at(643), [0x3a, 0x4e, 0x64, 255], 'the country whose brief is open');
assert.deepEqual(at(804), [0x28, 0x37, 0x48, 255], 'a country with events');
assert.deepEqual(at(112), [0x1c, 0x27, 0x34, 255], "a country of the chosen region");
assert.deepEqual(at(250), [0, 0, 0, 0], 'other countries are left as painted');
assert.ok(ctx.landIds.states.needsUpdate && puts === 1, 'the table goes up to the graphics chip');

ctx.landIds.states.needsUpdate = false;
ctx.paintStates();
assert.ok(!ctx.landIds.states.needsUpdate && puts === 1, 'nothing changed, nothing sent');
console.log('PASS: lit countries from a colour table: plain land at 0, brief, events and region colours by id; unchanged sets send nothing.');
