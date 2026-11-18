// Tension needles (the owner, 2026-11-17): one dial per region from pipeline/tension.py's readings.
// The needle's angle follows the reading, the level word and the day's arrow come from the data,
// a region without enough history has no needle, and a live update moves needles without
// rebuilding the strip (so they glide rather than jump).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('site/app.js', 'utf8');
const section = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));

class El {
  constructor() { this.hidden = true; this.innerHTML = ''; this.kids = new Map(); this.className = ''; this.attrs = {}; this.style = {}; this.builds = 0; }
  setAttribute(k, v) { this.attrs[k] = v; }
  querySelector(sel) {
    if (!this.kids.has(sel)) this.kids.set(sel, new El());
    return this.kids.get(sel);
  }
}
const box = new El();
const dials = new Map();
Object.defineProperty(box, 'innerHTML', {
  get() { return this._html || ''; },
  set(v) { this._html = v; this.builds++; dials.clear(); },
});
box.querySelector = (sel) => {
  const id = (sel.match(/data-tension="([^"]+)"/) || [])[1];
  if (!id || !box._html.includes(`data-tension="${id}"`)) return null;
  if (!dials.has(id)) { const d = new El(); d.kids.set('.tn-level', new El()); dials.set(id, d); }
  return dials.get(id);
};
const frames = [];
const ctx = {
  S: { region: 'world', data: null },
  document: { getElementById: (id) => (id === 'tension' ? box : null) },
  CSS: { escape: (s) => s },
  esc: (s) => String(s),
  clamp: (x, a, b) => Math.max(a, Math.min(b, x)),
  reduceMotion: false, isMobile: () => false, layout: () => {},
  requestAnimationFrame: (f) => frames.push(f),
  hideTensionCard: () => {}, tnCardFor: null,
};
vm.createContext(ctx);
vm.runInContext(section('  const TN_SHORT', '  function tensionCardHtml(') + '\nthis.renderTension = renderTension; this.needleAngle = needleAngle; this.tnArc = tnArc;', ctx);
const flush = () => { while (frames.length) frames.shift()(); };

assert.equal(ctx.needleAngle(0), -90, 'lowest reading points left');
assert.equal(ctx.needleAngle(0.5), 0, 'a usual day points straight up');
assert.equal(ctx.needleAngle(1), 90, 'highest points right');
assert.equal(ctx.needleAngle(7), 90, 'out-of-range readings stay on the dial');
assert.match(ctx.tnArc(0, 1, 46), /^M14\.00 56\.00A46 46 0 0 1 106\.00 56\.00$/, 'the dial runs over the top from left to right');

const data = (mid, prev) => ({ tension: { baseline_days: 30, window_hours: 24, regions: [
  { theater: 'mideast', name: 'Middle East', level: mid > 0.85 ? 'High' : 'Elevated', reading: mid, prev },
  { theater: 'latam', name: 'Latin America', level: null, days: 4 },
] } });
ctx.renderTension();
assert.equal(box.hidden, true, 'no readings, no strip');

ctx.S.data = data(0.9, 0.7);
ctx.renderTension();
assert.equal(box.hidden, false);
assert.equal(box.builds, 1);
assert.match(box.innerHTML, /data-tension="mideast"[\s\S]*tn-needle/, 'a region with a reading has a needle');
assert.doesNotMatch(box.innerHTML.slice(box.innerHTML.indexOf('data-tension="latam"')), /tn-needle/, 'one without enough history has none');
assert.match(box.innerHTML, /Each region against its own last 30 days/);
const me = dials.get('mideast');
assert.equal(me.kids.get('.tn-needle').style.transform, undefined, 'a new strip starts upright and swings to its reading');
flush();
assert.equal(me.kids.get('.tn-needle').style.transform, `rotate(${-90 + 0.9 * 180}deg)`);
assert.match(me.kids.get('.tn-level').innerHTML, /^High<i aria-hidden="true">▲<\/i>$/, 'up a tenth or more on the day before: an arrow');
assert.match(me.attrs['aria-label'], /Middle East: tension High, up on the day before\. Judged against this region's own last 30 days\./);
assert.equal(me.className, 'tn lv-high');
assert.equal(dials.get('latam').kids.get('.tn-level').innerHTML, 'No reading yet');

ctx.S.region = 'mideast';
ctx.S.data = data(0.7, 0.66);
ctx.renderTension();
assert.equal(box.builds, 1, 'a live update moves the needles in place, so they glide');
assert.equal(me.kids.get('.tn-needle').style.transform, `rotate(${-90 + 0.7 * 180}deg)`);
assert.equal(me.kids.get('.tn-level').innerHTML, 'Elevated', 'a small change: no arrow');
assert.equal(me.className, 'tn lv-elevated is-sel', 'the chosen region is marked');
assert.equal(me.attrs['aria-pressed'], 'true');
console.log('PASS: needle angle from reading, level and day arrow, no needle without history, glide from upright, live updates in place, chosen region marked.');
