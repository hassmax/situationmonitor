const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
const constant = name => source.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const section = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
const now = Date.now(), HOUR = 3600000, DAY = HOUR * 24;
const S = { windowH: 24, theaterOn: new Set(['indo-pacific']), theaters: [],
  statusOn: new Set(['unconfirmed']), off: new Set(), query: '', data: null, selectedId: null };
class Clock extends Date { static now() { return now; } }
const context = { S, HOUR, DAY, Date: Clock, LIVE_MS: 6 * HOUR, viewed: {}, lastSeen: now - HOUR,
  legendKey: e => e.type, typeLabel: e => e.type, tkind: e => e.transfer.kind,
  eventIcon: () => '<span>icon</span>', metaLine: e => e.place,
  STATUS: { unconfirmed: { conf: 'outline', label: 'Unconfirmed' } } };
context.markerPick = events => events;
context.buildSupply = () => ({ flows: [], pledges: [] });
vm.createContext(context);
vm.runInContext([
  constant('esc'), constant('isNew'), constant('isLive'),
  section('  function ago(ms)', '  const agoShort'), constant('agoShort'),
  section('  let wordsFor =', '  // One ring from'),
  section('  function itemHtml(e, names)', '  // Regional analysis'),
  section('  function frameData()', '  function renderLists('),
  'this.api = { passes, hasFollowup, visibleEvents, frameData, isNew, isLive, itemHtml };'
].join('\n'), context);
const api = context.api;
const incident = { id: 'original-dmz', type: 'explosion', place: 'Korean DMZ', status: 'unconfirmed',
  severity: 2, theater: 'indo-pacific', sources_count: 2, summary: 'A mine explosion injured soldiers.',
  _t: now - 6 * DAY, _tu: now - HOUR / 2, _search: 'mine explosion korean dmz' };
incident.time = new Date(incident._t).toISOString();
assert(!api.passes(incident), 'A fresh report cannot bring a six-day-old event into the 24-hour map');
assert(api.hasFollowup(incident));
assert(!api.isNew(incident), 'A follow-up never marks the original occurrence new');
assert(!api.isLive(incident), 'A follow-up never restarts live animation');
assert(api.itemHtml(incident, {}).includes('Updated '));
assert(api.itemHtml(incident, {}).includes(incident.time));
S.windowH = 6;
assert(!api.passes(incident), 'Six-hour window follows the occurrence date too');
incident._tu = now - 7 * HOUR;
assert(!api.passes(incident), 'Visibility does not depend on reporting activity');
incident._tu = now - HOUR / 2;
incident.alert = true;
assert(!api.passes(incident), 'Old warnings cannot resurface on later coverage');
incident.alert = false;
incident.possibly_old = true;
assert(!api.passes(incident), 'Doubtful old stories cannot resurface on later coverage');
incident.possibly_old = false;
incident.type = 'arms_transfer'; incident.transfer = { kind: 'delivery' };
assert(!api.passes(incident), 'Supply-route dates remain occurrence based');
incident.type = 'explosion'; delete incident.transfer;
const newer = { ...incident, id: 'new-incident', _t: now - 2 * HOUR, _tu: now - 2 * HOUR };
newer.time = new Date(newer._t).toISOString();
S.data = { events: [incident, newer] };
assert.equal(api.visibleEvents()[0].id, newer.id, 'Sorting stays newest occurrence first');
assert.equal(api.visibleEvents().length, 1, 'Only the new occurrence appears in the short window');
for (const hours of [6, 24, 72, 168]) {
  S.windowH = hours;
  const boundary = { ...newer, _t: now - hours * HOUR, _tu: now };
  assert(api.passes(boundary), `${hours}-hour boundary is included`);
  assert(!api.passes({ ...boundary, _t: boundary._t - 1 }), `${hours}-hour window hides older events despite a current report`);
}
S.windowH = 168;
assert(api.passes(incident), 'A longer window shows the original event with its grouped reports');
S.windowH = 24;
S.selectedId = incident.id;
assert(!api.frameData().mapEvents.some(e => e.id === incident.id), 'Selecting an old alert cannot bypass the time window');
S.query = 'unrelated';
assert(!api.passes(newer), 'New alerts respect the search filter');
S.query = '';
S.off.add('explosion');
assert(!api.passes(newer), 'New alerts respect hidden categories');
assert(!source.includes('activityTime('), 'Opening view and confidence counts also use occurrence dates');
assert(!source.includes('Recent reports keep it on the map'), 'Detail copy matches strict event windows');
assert(source.includes('if (fresh && passes(e)) playLaunches(e, flight)'), 'Opening an old shared alert cannot draw its launch paths');
console.log('PASS: strict 6h/24h/3d/7d occurrence windows, exact boundaries, fresh reports cannot revive old events, grouped history stays accessible in longer windows, ordering and filters.');
