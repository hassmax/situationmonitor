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
    '\nthis.api = { APP_ROOT, INITIAL_EVENT_ID, eventLink, eventIdFrom, sharedSnapshot };', context);
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
