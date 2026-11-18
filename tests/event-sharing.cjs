const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
const section = (from, to) => code.slice(code.indexOf(from), code.indexOf(to, code.indexOf(from)));
function sharing(url, base, snapshot = null, demo = false) {
  const baseElement = { href: base };
  const context = { URL, DEMO: demo, location: new URL(url),
    document: { baseURI: base, querySelector: () => baseElement,
      getElementById: () => snapshot ? { textContent: JSON.stringify(snapshot) } : null } };
  vm.createContext(context);
  vm.runInContext(section('  const APP_ROOT =', '  // This page\'s own version:') +
    '\nthis.api = { APP_ROOT, INITIAL_EVENT_ID, INITIAL_LINK, eventLink, routeLink, needleLink, eventIdFrom, sharedSnapshot };', context);
  return { ...context.api, baseElement };
}
const root = 'https://hassmax.github.io/situationmonitor/';
const id = 'a99b66331f72';
const path = root + 'events/' + id + '/';
let a = sharing(root + '#' + id + ',', root);
assert.equal(a.INITIAL_EVENT_ID, id);
assert.equal(a.eventLink(id), path);
assert.equal(a.eventIdFrom('%zz'), '');
a = sharing(path, root, { id });
assert.equal(a.INITIAL_EVENT_ID, id);
assert.equal(a.APP_ROOT.href, root);
assert.equal(a.baseElement.href, root);
assert.equal(a.eventLink('123456abcdef'), root + 'events/123456abcdef/');
assert.equal(a.sharedSnapshot.id, id);
a = sharing(root + '?demo#' + id, root, null, true);
assert.equal(a.eventLink(id), root + '?demo#' + id);
const context = { S: { windowH: 24 }, HOUR: 3600000, DAY: 86400000, viewed: {}, lastSeen: 0,
  LIVE_MS: 21600000, Date, onMap: () => true };
vm.createContext(context);
vm.runInContext(code.match(/^  const isNew = .*$/m)[0] + '\n' + code.match(/^  const isLive = .*$/m)[0] + '\n' +
  section('  function passes(e,', '  const visibleEvents =') + '\nthis.api = { passes, isNew, isLive };', context);
const archived = { id, _archived: true, _t: Date.now() };
assert(!context.api.passes(archived));
assert(!context.api.isNew(archived));
assert(!context.api.isLive(archived));
assert(code.includes('id="copyEventLink"'));
assert(code.includes('navigator.clipboard.writeText(eventLink(e.id))'));
assert(code.includes('APP_ROOT.pathname + location.search + "#" + encodeURIComponent(hull)'));
console.log('PASS: share URL, base paths, old links and trailing commas, demo links, archived visibility and copy action.');

// Supply routes and tension needles (the owner, 2026-11-18: "allow an option to share links to the
// needles, and the supply route events too"): links on the main page, opened on arrival.
{
  const key = 'Ramstein>US>UA|aid|RAF Fairford>Ramstein';
  let b = sharing(root, root);
  assert.equal(b.routeLink(key), root + '#route=' + encodeURIComponent(key));
  assert.equal(b.needleLink('mideast'), root + 'needles/mideast/', 'a needle link is its own page, with a preview of the dial');
  assert.equal(b.INITIAL_LINK, null);
  b = sharing(b.routeLink(key), root);
  assert.deepEqual({ ...b.INITIAL_LINK }, { kind: 'route', id: key }, 'a route link opens that route');
  assert.equal(b.INITIAL_EVENT_ID, '', 'and is not taken for an event id');
  b = sharing(root + 'needles/horn/', root);
  assert.deepEqual({ ...b.INITIAL_LINK }, { kind: 'tension', id: 'horn' }, 'the needle page opens that needle');
  assert.equal(b.APP_ROOT.href, root, 'from the site root');
  assert.equal(b.INITIAL_EVENT_ID, '');
  b = sharing(root + '#tension=drc_sahel', root);
  assert.deepEqual({ ...b.INITIAL_LINK }, { kind: 'tension', id: 'drc_sahel' });
  assert.equal(b.INITIAL_EVENT_ID, '');
  assert.equal(sharing(root + '#route=%zz', root).INITIAL_LINK, null, 'a broken link opens the plain page');
  b = sharing(root + '?demo', root, null, true);
  assert.equal(b.needleLink('ukraine'), root + '?demo#tension=ukraine', 'demo links stay on the demo');
  assert.equal(b.routeLink('US>UA'), root + '?demo#route=US%3EUA');
  console.log('PASS: route links and needle pages built, read on arrival (old #tension links too), kept apart from event ids, demo kept.');
}
