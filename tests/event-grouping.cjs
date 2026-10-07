const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
const constant = name => source.match(new RegExp('^  const ' + name + ' = .*$', 'm'))[0];
const section = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
const now = Date.now(), HOUR = 3600000, DAY = HOUR * 24;
const S = { windowH: 24, theaterOn: new Set(['indo-pacific']), theaters: [],
  statusOn: new Set(['unconfirmed']), off: new Set(), query: '', data: null, selectedId: null };
const context = { S, HOUR, DAY, LIVE_MS: 6 * HOUR, viewed: {}, lastSeen: now - HOUR,
  legendKey: e => e.type, typeLabel: e => e.type, tkind: e => e.transfer.kind,
  eventIcon: () => '<span>icon</span>', metaLine: e => e.place,
  STATUS: { unconfirmed: { conf: 'outline', label: 'Unconfirmed' } } };
vm.createContext(context);
vm.runInContext([
  constant('esc'), constant('isNew'), constant('isLive'),
  section('  function ago(ms)', '  const agoShort'), constant('agoShort'),
  section('  let wordsFor =', '  // One ring from'),
  section('  function itemHtml(e, names)', '  // Regional analysis'),
  'this.api = { passes, activityTime, hasFollowup, visibleEvents, isNew, isLive, itemHtml };'
].join('\n'), context);
const api = context.api;
const incident = { id: 'original-dmz', type: 'explosion', place: 'Korean DMZ', status: 'unconfirmed',
  severity: 2, theater: 'indo-pacific', sources_count: 2, summary: 'A mine explosion injured soldiers.',
  _t: now - 6 * DAY, _tu: now - HOUR / 2, _search: 'mine explosion korean dmz' };
incident.time = new Date(incident._t).toISOString();
assert(api.passes(incident), 'Recent coverage keeps an older incident in the 24-hour map');
assert(api.hasFollowup(incident));
assert(!api.isNew(incident), 'A follow-up never marks the original occurrence new');
assert(!api.isLive(incident), 'A follow-up never restarts live animation');
assert(api.itemHtml(incident, {}).includes('Updated '));
assert(api.itemHtml(incident, {}).includes(incident.time));
S.windowH = 6;
assert(api.passes(incident), 'Six-hour window also follows current report activity');
incident._tu = now - 7 * HOUR;
assert(!api.passes(incident), 'The incident leaves the window once reporting is quiet');
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
S.data = { events: [incident, newer] };
assert.equal(api.visibleEvents()[0].id, newer.id, 'Sorting stays newest occurrence first');
S.query = 'unrelated';
assert(!api.passes(incident), 'Follow-ups respect the search filter');
S.query = '';
S.off.add('explosion');
assert(!api.passes(incident), 'Follow-ups respect hidden categories');
console.log('PASS: activity windows, quiet expiry, original date, Updated label, no new/live reset, warning/old-story/supply exceptions, ordering and filters.');
