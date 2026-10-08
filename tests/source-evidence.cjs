const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/../site/app.js', 'utf8');
const start = source.indexOf('  const reportTime =');
const end = source.indexOf('\n  function ', source.indexOf('  function reportsHtml(', start) + 25);
const context = { Date, Map, Number, esc: s => String(s ?? '').replaceAll('<', '&lt;'),
  safeUrl: s => s, fmtEvidenceTime: s => String(s), ago: () => '1 hour ago',
  PLATFORM: { rss: 'News feed' }, KIND: { news: 'News' } };
vm.createContext(context);
vm.runInContext(source.slice(start, end) + '\nthis.render = reportsHtml;', context);
const reports = ['Reuters', 'BBC'].map(source => ({ source, platform: 'rss', kind: 'news', side: null,
  claim_source: 'idf', summary: 'IDF says it struck a depot.', url: 'https://example.com/' + source,
  time: '2026-10-07T12:00:00Z' }));
const html = context.render(reports, {});
assert(html.includes('Reuters') && html.includes('BBC'));
assert.equal((html.match(/, IDF statement/g) || []).length, 2);
assert(html.includes("count together as Israel's side"));
assert(!html.includes('aligned with IL'), 'Repeating a claim does not mark the newsroom itself as aligned');
assert(!context.render([{ ...reports[0], claim_source: null }], {}).includes('IDF statement'));
console.log('PASS: IDF evidence labels, publisher identity, and independent reporting display.');
